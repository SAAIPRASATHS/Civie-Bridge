"""Phase 6 - LangGraph orchestration of the CivicBridge agents.

        START
          |
          v
      intake --------(unusable)---------------------------------> assemble
          |   \\------(too thin)---------------------------------> human_review
          v
    classification --(failed / undetermined / low confidence)--> human_review
          |
          v
  duplicate_detection --(likely duplicate)---------------------> human_review
          |
          v
   evidence_analysis
          |
          v
       routing ---------(missing / failed)----------------------> human_review
          |
          v
      resolution ------(needs human review)---------------------> human_review
          |                                                          |
          v                                                          |
       assemble <----------------------------------------------------+
          |
         END

The graph reuses the Phase 3-5 agents unchanged: :class:`IntakeAgent`,
:class:`ClassificationAgent`, :class:`DuplicateDetectionAgent`,
:class:`EvidenceAnalysisAgent`, :class:`RoutingAgent` and
:class:`ResolutionAgent`. Nothing here re-implements agent logic; each node is a
thin, timed, retry-bounded wrapper that records what happened.

Design boundaries enforced in code, not just documented:

* **The graph is pure.** No node touches the database. Persistence and the
  ``agent_execution_logs`` rows are written once, *after* the graph finishes, by
  the runner - so a retry or a resumed run can never write a duplicate complaint
  or duplicate step log.
* **No consequential action.** The workflow assigns no department, links no
  duplicate, resolves, rejects or closes nothing and writes no status-history
  row. It only produces validated agent outputs plus review requirements; the
  Phase 4/5 officer endpoints remain the sole decision paths.
* **A human-review interrupt is not an authorisation.** When resumable
  interruption is enabled, the resume payload is recorded as an acknowledgement
  only. Resuming never triggers an assignment, merge, rejection, resolution or
  closure.
* **Runtime-only inputs stay out of the checkpoint.** Raw image bytes and the
  candidate complaints to compare against are passed through the graph config,
  never through :class:`~app.schemas.workflow.WorkflowState`, so a serialised
  checkpoint never contains them.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.duplicate import ComplaintSnapshot
from app.agents.errors import AgentError, AgentTimeoutError
from app.agents.evidence import EvidenceImage
from app.agents.extraction import MISSING_DETAIL
from app.agents.persistence import persist_analysis
from app.agents.pipeline import ComplaintPipeline
from app.agents.resolution import ResolutionAgent, ResolutionInput
from app.agents.routing import RoutingAgent, RoutingInput
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import get_logger
from app.models.agent_execution_log import AgentExecutionLog
from app.models.complaint import Complaint
from app.models.enums import AgentExecutionStatus, AgentName
from app.schemas.agent import ComplaintAnalysis
from app.schemas.workflow import NodeTrace, WorkflowOutcome, WorkflowRunResult, WorkflowState

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Node names (also the AgentExecutionLog.step_index ordering and trace keys)
# ---------------------------------------------------------------------------

NODE_INTAKE = "intake"
NODE_CLASSIFICATION = "classification"
NODE_DUPLICATE = "duplicate_detection"
NODE_EVIDENCE = "evidence_analysis"
NODE_ROUTING = "routing"
NODE_RESOLUTION = "resolution"
NODE_HUMAN_REVIEW = "human_review"
NODE_ASSEMBLE = "assemble"

#: Upper bound on a stored error summary, so a message can never dominate a log.
MAX_ERROR_CHARS = 300


# ---------------------------------------------------------------------------
# Runtime inputs (never checkpointed) and dependencies
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WorkflowInputs:
    """The non-serialisable inputs a single run needs, passed through config."""

    candidates: Sequence[ComplaintSnapshot] = ()
    images: Sequence[EvidenceImage] = ()
    interrupt_for_review: bool = False
    persist: bool = False


@dataclass(slots=True)
class WorkflowDependencies:
    """The agents the graph drives; every one is an existing implementation."""

    pipeline: ComplaintPipeline
    routing: RoutingAgent
    resolution: ResolutionAgent
    settings: Settings

    @classmethod
    def build(
        cls,
        *,
        settings: Settings | None = None,
        pipeline: ComplaintPipeline | None = None,
    ) -> WorkflowDependencies:
        cfg = settings or default_settings
        return cls(
            pipeline=pipeline or ComplaintPipeline.build(settings=cfg),
            routing=RoutingAgent(),
            resolution=ResolutionAgent(),
            settings=cfg,
        )


# ---------------------------------------------------------------------------
# Timeout and retry primitives
# ---------------------------------------------------------------------------


def _call_with_timeout(action: Callable[[], Any], timeout_seconds: float | None) -> Any:
    """Run ``action`` with a wall-clock ceiling.

    Python cannot safely kill a running thread, so a timed-out call is abandoned
    rather than interrupted (the underlying Ollama request is separately bounded
    by ``ollama_llm_timeout``). The executor is shut down without waiting so the
    workflow is never blocked by a stalled call.
    """
    if not timeout_seconds or timeout_seconds <= 0:
        return action()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(action)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeout as exc:
        raise AgentTimeoutError(
            f"node exceeded the {timeout_seconds:.0f}s timeout and was abandoned"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _safe_error(exc: BaseException) -> str:
    """A short, safe error summary - never a full traceback or raw secret."""
    if isinstance(exc, AgentError):
        return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
    # An unexpected exception may carry internal detail, so only its type is kept.
    return f"{type(exc).__name__}: unexpected workflow error"[:MAX_ERROR_CHARS]


@dataclass(slots=True)
class _NodeOutcome:
    """Result of one node attempt sequence."""

    result: Any | None
    status: AgentExecutionStatus
    attempt: int
    duration_ms: float
    error: str | None = None
    degraded: bool = False


def _execute(
    action: Callable[[], Any],
    *,
    settings: Settings,
    timeout_seconds: float | None = None,
) -> _NodeOutcome:
    """Run one agent action with a timeout and a bounded number of retries.

    Only transient agent/timeout errors are retried. A success returns on the
    first attempt, so retries can never duplicate a side effect, and a
    programming error (anything that is not an :class:`AgentError`) fails
    immediately rather than being retried blindly.
    """
    attempts = max(1, settings.orchestration_max_node_attempts)
    timeout = (
        settings.orchestration_node_timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    last_error: str | None = None
    duration_ms = 0.0

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            result = _call_with_timeout(action, timeout)
        except AgentError as exc:  # transient: timeout, transport or output error
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            last_error = _safe_error(exc)
        except Exception as exc:  # a bug: fail the node, do not retry
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            return _NodeOutcome(
                result=None,
                status=AgentExecutionStatus.FAILED,
                attempt=attempt,
                duration_ms=duration_ms,
                error=_safe_error(exc),
                degraded=True,
            )
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            return _NodeOutcome(
                result=result,
                status=AgentExecutionStatus.SUCCEEDED,
                attempt=attempt,
                duration_ms=duration_ms,
            )

    return _NodeOutcome(
        result=None,
        status=AgentExecutionStatus.FAILED,
        attempt=attempts,
        duration_ms=duration_ms,
        error=last_error,
        degraded=True,
    )


def _record(
    state: WorkflowState,
    node: str,
    *,
    duration_ms: float,
    status: AgentExecutionStatus,
    **extra: Any,
) -> dict[str, Any]:
    """Append one trace entry and return the partial state update."""
    entry = NodeTrace(node=node, status=status, duration_ms=duration_ms, **extra)
    return {
        "trace": [*state.trace, entry],
        "node_timings_ms": {**state.node_timings_ms, node: duration_ms},
        "current_node": node,
    }


def _fail(
    state: WorkflowState,
    node: str,
    outcome: _NodeOutcome,
    *,
    label: str,
) -> dict[str, Any]:
    """Record a failed node, mark the run degraded, and let the graph continue.

    A failure is never fatal to the graph: the trace, the error summary and the
    degraded flags are recorded, and the conditional edges decide whether a
    human must take over. Nothing is invented in place of the failed result.
    """
    error = outcome.error or "unknown error"
    return _record(
        state,
        node,
        duration_ms=outcome.duration_ms,
        status=outcome.status,
        attempt=outcome.attempt,
        error=outcome.error,
        degraded=True,
    ) | {
        "errors": [*state.errors, f"{label}: {error}"],
        "degraded": True,
        "degraded_reasons": [
            *state.degraded_reasons,
            f"{label} failed; the workflow continued without it",
        ],
    }


def _configurable(config: RunnableConfig | None) -> dict[str, Any]:
    if not config:
        return {}
    return dict(config.get("configurable") or {})


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _build_nodes(deps: WorkflowDependencies) -> dict[str, Callable[..., dict[str, Any]]]:
    settings = deps.settings

    def intake_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        text = state.complaint_text
        if not text or not text.strip():
            empty = _NodeOutcome(
                result=None,
                status=AgentExecutionStatus.FAILED,
                attempt=1,
                duration_ms=0.0,
                error="the complaint text is empty",
                degraded=True,
            )
            return _fail(state, NODE_INTAKE, empty, label="intake")

        outcome = _execute(lambda: deps.pipeline.intake.run(text), settings=settings)
        if outcome.result is None:
            return _fail(state, NODE_INTAKE, outcome, label="intake")

        intake = outcome.result
        updates: dict[str, Any] = {"intake": intake}
        if intake.degraded:
            updates["degraded"] = True
            updates["degraded_reasons"] = [
                *state.degraded_reasons,
                "intake used the deterministic fallback",
            ]
        # An intake that could not find any usable description is too thin to
        # classify; it is routed straight to human review.
        if MISSING_DETAIL in intake.missing_information:
            updates["needs_human_review"] = True
            updates["review_reasons"] = [
                *state.review_reasons,
                "the complaint description is too thin to classify automatically",
            ]
        return (
            _record(
                state,
                NODE_INTAKE,
                duration_ms=outcome.duration_ms,
                status=outcome.status,
                attempt=outcome.attempt,
                degraded=intake.degraded,
            )
            | updates
        )

    def classification_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        if state.intake is None:
            return _record(
                state,
                NODE_CLASSIFICATION,
                duration_ms=0.0,
                status=AgentExecutionStatus.SKIPPED,
                skipped=True,
                skipped_reason="no intake result to classify",
            )

        outcome = _execute(
            lambda: deps.pipeline.classification.run(state.intake), settings=settings
        )
        if outcome.result is None:
            return _fail(state, NODE_CLASSIFICATION, outcome, label="classification")

        classification = outcome.result
        updates: dict[str, Any] = {"classification": classification}
        if classification.degraded:
            updates["degraded"] = True
            updates["degraded_reasons"] = [
                *state.degraded_reasons,
                "classification used the deterministic fallback",
            ]
        if classification.needs_human_review:
            updates["needs_human_review"] = True
            updates["review_reasons"] = [
                *state.review_reasons,
                classification.review_reason
                or "the classification confidence is below the review threshold",
            ]
        return (
            _record(
                state,
                NODE_CLASSIFICATION,
                duration_ms=outcome.duration_ms,
                status=outcome.status,
                attempt=outcome.attempt,
                degraded=classification.degraded,
            )
            | updates
        )

    def duplicate_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        candidates = list(_configurable(config).get("candidates") or [])
        if not candidates or state.intake is None:
            return _record(
                state,
                NODE_DUPLICATE,
                duration_ms=0.0,
                status=AgentExecutionStatus.SKIPPED,
                skipped=True,
                skipped_reason="no candidate complaints were supplied to compare against",
            )

        outcome = _execute(
            lambda: deps.pipeline.duplicate.run_intake(
                state.intake,
                candidates,
                classification=state.classification,
                latitude=state.latitude,
                longitude=state.longitude,
            ),
            settings=settings,
        )
        if outcome.result is None:
            return _fail(state, NODE_DUPLICATE, outcome, label="duplicate detection")

        duplicate = outcome.result
        updates: dict[str, Any] = {"duplicate": duplicate}
        if duplicate.degraded:
            updates["degraded"] = True
            updates["degraded_reasons"] = [
                *state.degraded_reasons,
                "duplicate detection used the deterministic fallback",
            ]
        if duplicate.likely_duplicate_found:
            updates["needs_human_review"] = True
            updates["review_reasons"] = [
                *state.review_reasons,
                duplicate.review_reason
                or "a likely duplicate was found; a human must confirm any link",
            ]
        return (
            _record(
                state,
                NODE_DUPLICATE,
                duration_ms=outcome.duration_ms,
                status=outcome.status,
                attempt=outcome.attempt,
                degraded=duplicate.degraded,
            )
            | updates
        )

    def evidence_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        images = list(_configurable(config).get("images") or [])
        if not images:
            return _record(
                state,
                NODE_EVIDENCE,
                duration_ms=0.0,
                status=AgentExecutionStatus.SKIPPED,
                skipped=True,
                skipped_reason="no images were supplied for evidence analysis",
            )

        category = state.classification.category if state.classification else None
        complaint_text = state.intake.normalized_description if state.intake else None

        def analyse() -> list[Any]:
            return [
                deps.pipeline.evidence.run(image, category=category, complaint_text=complaint_text)
                for image in images
            ]

        outcome = _execute(analyse, settings=settings)
        if outcome.result is None:
            return _fail(state, NODE_EVIDENCE, outcome, label="evidence analysis")

        analysed = list(outcome.result)
        updates: dict[str, Any] = {"evidence": analysed}
        if any(item.degraded for item in analysed):
            updates["degraded"] = True
            updates["degraded_reasons"] = [
                *state.degraded_reasons,
                "evidence analysis used the deterministic fallback",
            ]
        return (
            _record(
                state,
                NODE_EVIDENCE,
                duration_ms=outcome.duration_ms,
                status=outcome.status,
                attempt=outcome.attempt,
                degraded=any(item.degraded for item in analysed),
            )
            | updates
        )

    def routing_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        category = state.classification.category if state.classification else None
        payload = RoutingInput(
            text=state.complaint_text,
            category=category,
            ward=state.intake.ward if state.intake else None,
            location_text=state.intake.location_text if state.intake else None,
            latitude=state.latitude,
            longitude=state.longitude,
        )
        outcome = _execute(lambda: deps.routing.run(payload), settings=settings)
        if outcome.result is None:
            return _fail(state, NODE_ROUTING, outcome, label="routing")

        routing = outcome.result
        updates: dict[str, Any] = {"routing": routing}
        if routing.requires_human_review:
            updates["needs_human_review"] = True
            updates["review_reasons"] = [
                *state.review_reasons,
                routing.review_reason or "the routing recommendation needs human confirmation",
            ]
        return (
            _record(
                state,
                NODE_ROUTING,
                duration_ms=outcome.duration_ms,
                status=outcome.status,
                attempt=outcome.attempt,
            )
            | updates
        )

    def resolution_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        category = state.classification.category if state.classification else None
        available = tuple(
            item.file_name or f"evidence_{index}"
            for index, item in enumerate(state.evidence, start=1)
        )
        payload = ResolutionInput(
            text=state.complaint_text,
            category=category,
            ward=state.intake.ward if state.intake else None,
            available_evidence=available,
        )
        outcome = _execute(lambda: deps.resolution.run(payload), settings=settings)
        if outcome.result is None:
            return _fail(state, NODE_RESOLUTION, outcome, label="resolution recommendation")

        resolution = outcome.result
        updates: dict[str, Any] = {"resolution": resolution}
        if resolution.requires_human_review:
            updates["needs_human_review"] = True
            updates["review_reasons"] = [
                *state.review_reasons,
                resolution.review_reason or "the resolution recommendation needs human review",
            ]
        return (
            _record(
                state,
                NODE_RESOLUTION,
                duration_ms=outcome.duration_ms,
                status=outcome.status,
                attempt=outcome.attempt,
            )
            | updates
        )

    def human_review_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        """Record the review requirement, interrupting only when asked.

        The interrupt exists so a caller can pause and resume a run. The resume
        payload is treated purely as an acknowledgement - it authorises nothing.
        """
        reasons = state.review_reasons or ["the workflow flagged this complaint for human review"]
        started = time.perf_counter()

        if _configurable(config).get("interrupt"):
            decision = interrupt(
                {
                    "workflow_run_id": state.workflow_run_id,
                    "review_required": True,
                    "review_reasons": reasons,
                    "message": (
                        "Human review is required. Resuming records an acknowledgement "
                        "only; it does not authorise any assignment, merge, rejection, "
                        "resolution or closure."
                    ),
                }
            )
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            payload = decision if isinstance(decision, dict) else {}
            reviewer_id = payload.get("reviewer_id")
            return _record(
                state,
                NODE_HUMAN_REVIEW,
                duration_ms=duration_ms,
                status=AgentExecutionStatus.AWAITING_HUMAN,
            ) | {
                "needs_human_review": True,
                "review_reasons": reasons,
                "human_review_completed": True,
                "reviewer_id": str(reviewer_id) if reviewer_id else None,
                "resume_note": payload.get("note"),
            }

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        return _record(
            state,
            NODE_HUMAN_REVIEW,
            duration_ms=duration_ms,
            status=AgentExecutionStatus.AWAITING_HUMAN,
        ) | {"needs_human_review": True, "review_reasons": reasons}

    def assemble_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        started = time.perf_counter()
        if state.intake is None:
            outcome = WorkflowOutcome.FAILED
        elif state.needs_human_review:
            outcome = WorkflowOutcome.AWAITING_HUMAN_REVIEW
        elif state.degraded:
            outcome = WorkflowOutcome.DEGRADED
        else:
            outcome = WorkflowOutcome.COMPLETED
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        return _record(
            state,
            NODE_ASSEMBLE,
            duration_ms=duration_ms,
            status=AgentExecutionStatus.SUCCEEDED,
        ) | {"outcome": outcome, "completed_at": datetime.now(UTC)}

    return {
        NODE_INTAKE: intake_node,
        NODE_CLASSIFICATION: classification_node,
        NODE_DUPLICATE: duplicate_node,
        NODE_EVIDENCE: evidence_node,
        NODE_ROUTING: routing_node,
        NODE_RESOLUTION: resolution_node,
        NODE_HUMAN_REVIEW: human_review_node,
        NODE_ASSEMBLE: assemble_node,
    }


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------


def route_after_intake(state: WorkflowState) -> str:
    """An unusable complaint stops; a too-thin one goes straight to a human."""
    if state.intake is None:
        return NODE_ASSEMBLE
    if MISSING_DETAIL in state.intake.missing_information:
        return NODE_HUMAN_REVIEW
    return NODE_CLASSIFICATION


def route_after_classification(state: WorkflowState) -> str:
    """A failed, undetermined or low-confidence classification goes to a human."""
    if state.classification is None:
        return NODE_HUMAN_REVIEW
    if state.classification.category is None or state.classification.needs_human_review:
        return NODE_HUMAN_REVIEW
    return NODE_DUPLICATE


def route_after_duplicate(state: WorkflowState) -> str:
    """A potential duplicate is put in front of a human before anything else."""
    if state.duplicate is not None and state.duplicate.likely_duplicate_found:
        return NODE_HUMAN_REVIEW
    return NODE_EVIDENCE


def route_after_evidence(state: WorkflowState) -> str:
    return NODE_ROUTING


def route_after_routing(state: WorkflowState) -> str:
    """A missing routing recommendation is an agent failure a human must triage."""
    if state.routing is None:
        return NODE_HUMAN_REVIEW
    return NODE_RESOLUTION


def route_after_resolution(state: WorkflowState) -> str:
    if state.resolution is None:
        return NODE_ASSEMBLE
    if state.needs_human_review:
        return NODE_HUMAN_REVIEW
    return NODE_ASSEMBLE


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_workflow_graph(
    deps: WorkflowDependencies,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the orchestration graph for the given dependencies."""
    builder = StateGraph(WorkflowState)
    for name, node in _build_nodes(deps).items():
        builder.add_node(name, node)

    builder.add_edge(START, NODE_INTAKE)
    builder.add_conditional_edges(
        NODE_INTAKE,
        route_after_intake,
        {
            NODE_CLASSIFICATION: NODE_CLASSIFICATION,
            NODE_HUMAN_REVIEW: NODE_HUMAN_REVIEW,
            NODE_ASSEMBLE: NODE_ASSEMBLE,
        },
    )
    builder.add_conditional_edges(
        NODE_CLASSIFICATION,
        route_after_classification,
        {
            NODE_DUPLICATE: NODE_DUPLICATE,
            NODE_HUMAN_REVIEW: NODE_HUMAN_REVIEW,
            NODE_ASSEMBLE: NODE_ASSEMBLE,
        },
    )
    builder.add_conditional_edges(
        NODE_DUPLICATE,
        route_after_duplicate,
        {NODE_EVIDENCE: NODE_EVIDENCE, NODE_HUMAN_REVIEW: NODE_HUMAN_REVIEW},
    )
    builder.add_conditional_edges(
        NODE_EVIDENCE,
        route_after_evidence,
        {NODE_ROUTING: NODE_ROUTING},
    )
    builder.add_conditional_edges(
        NODE_ROUTING,
        route_after_routing,
        {
            NODE_RESOLUTION: NODE_RESOLUTION,
            NODE_HUMAN_REVIEW: NODE_HUMAN_REVIEW,
        },
    )
    builder.add_conditional_edges(
        NODE_RESOLUTION,
        route_after_resolution,
        {NODE_HUMAN_REVIEW: NODE_HUMAN_REVIEW, NODE_ASSEMBLE: NODE_ASSEMBLE},
    )
    builder.add_edge(NODE_HUMAN_REVIEW, NODE_ASSEMBLE)
    builder.add_edge(NODE_ASSEMBLE, END)
    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


def build_checkpointer() -> InMemorySaver:
    """An in-memory checkpointer with the workflow's own state types allowed.

    LangGraph serialises checkpointed state with msgpack and warns - and, in a
    future release, refuses - for types it does not recognise. Only
    CivicBridge's own validated models are whitelisted here: no external or
    citizen-supplied object is ever allowed into a checkpoint, and the runtime
    inputs (candidate snapshots, image bytes) never reach one at all.
    """
    allowed: list[tuple[str, str]] = [
        ("app.models.enums", name)
        for name in (
            "AgentExecutionStatus",
            "ComplaintCategory",
            "ComplaintLanguage",
            "ComplaintPriority",
            "EvidenceVerificationStatus",
        )
    ]
    allowed += [
        ("app.schemas.agent", name)
        for name in (
            "AssumptionFinding",
            "ClassificationResult",
            "DuplicateCandidate",
            "DuplicateDetectionResult",
            "EvidenceAnalysisResult",
            "IntakeResult",
            "ObservableFinding",
            "ResolutionRecommendation",
            "RoutingRecommendation",
        )
    ]
    allowed += [
        ("app.schemas.workflow", name) for name in ("NodeTrace", "WorkflowOutcome", "WorkflowState")
    ]
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=allowed))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class ComplaintWorkflow:
    """Runs the graph, then persists and summarises the outcome exactly once."""

    deps: WorkflowDependencies
    checkpointer: Any | None = None
    graph: Any | None = None

    def __post_init__(self) -> None:
        if self.graph is None:
            self.graph = build_workflow_graph(self.deps, checkpointer=self.checkpointer)

    # -- invocation --------------------------------------------------------

    def invoke(
        self,
        *,
        text: str,
        inputs: WorkflowInputs | None = None,
        workflow_run_id: str | None = None,
        request_id: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> WorkflowRunResult:
        """Run the whole workflow for one complaint and return its summary."""
        inputs = inputs or WorkflowInputs()
        run_id = workflow_run_id or request_id or f"wf-{uuid.uuid4().hex[:16]}"
        state = WorkflowState(
            workflow_run_id=run_id,
            request_id=request_id,
            complaint_text=text,
            latitude=latitude,
            longitude=longitude,
        )
        return self._drive(state, inputs)

    def resume(
        self,
        *,
        workflow_run_id: str,
        reviewer_id: str | None,
        note: str | None = None,
        inputs: WorkflowInputs | None = None,
    ) -> WorkflowRunResult:
        """Resume a paused run. ``reviewer_id`` is an acknowledgement, not an authorisation."""
        inputs = inputs or WorkflowInputs()
        payload = {"reviewer_id": reviewer_id, "note": note}
        config = self._config(WorkflowInputs(interrupt_for_review=True), thread_id=workflow_run_id)
        result = self.graph.invoke(Command(resume=payload), config=config)
        return self._finish(result, inputs, thread_id=workflow_run_id)

    # -- internals ---------------------------------------------------------

    def _config(self, inputs: WorkflowInputs, *, thread_id: str) -> dict[str, Any]:
        configurable: dict[str, Any] = {
            "thread_id": thread_id,
            "candidates": list(inputs.candidates),
            "images": list(inputs.images),
            "interrupt": inputs.interrupt_for_review,
            "persist": inputs.persist,
        }
        return {"configurable": configurable}

    def _drive(self, state: WorkflowState, inputs: WorkflowInputs) -> WorkflowRunResult:
        config = self._config(inputs, thread_id=state.workflow_run_id)
        result = self.graph.invoke(state, config=config)
        return self._finish(result, inputs, thread_id=state.workflow_run_id)

    def _finish(self, result: Any, inputs: WorkflowInputs, *, thread_id: str) -> WorkflowRunResult:
        if isinstance(result, WorkflowState):
            final = result
            interrupted = False
        else:
            payload = dict(result)
            interrupt_info = payload.pop("__interrupt__", None)
            interrupted = bool(interrupt_info)
            final = WorkflowState.model_validate(payload)

        if interrupted:
            # A paused run has no outcome yet; return the requirements so the
            # caller knows exactly why a human is needed.
            return self._summarise(final, persisted=False, interrupted=True)

        if inputs.persist:
            self._persist(final, inputs)
        return self._summarise(final, persisted=final.persisted, interrupted=False)

    def _persist(self, state: WorkflowState, inputs: WorkflowInputs) -> None:
        """Write the complaint and its step logs exactly once per run.

        Persistence runs only after a run has completed (never on an interrupt),
        so a resumed run or a retried node cannot create a second complaint.
        """
        from app.database.session import session_scope

        with session_scope() as session:
            existing = self._existing_complaint_id(session, state.workflow_run_id)
            if existing is not None:
                state.complaint_id = str(existing)
                state.persisted = True
                return

            if state.intake is None or state.classification is None:
                # Nothing usable to persist; a failed run must not create a row.
                return

            analysis = ComplaintAnalysis(
                source_text=state.complaint_text,
                intake=state.intake,
                classification=state.classification,
                duplicate=state.duplicate,
                evidence=state.evidence,
                reference_id=state.reference_id,
                llm_calls=sum(
                    int(bool(item.llm_used))
                    for item in (state.intake, state.classification, state.duplicate)
                    if item is not None
                )
                + sum(int(bool(item.llm_used)) for item in state.evidence),
                total_latency_ms=round(
                    sum(state.node_timings_ms.values()),
                    1,
                ),
                steps=[entry.node for entry in state.trace],
                generated_at=state.started_at,
            )
            complaint = persist_analysis(
                session,
                analysis,
                is_synthetic=True,
                workflow_run_id=state.workflow_run_id,
            )
            self._append_workflow_step_logs(session, complaint, state)
            state.complaint_id = str(complaint.id)
            state.reference_id = complaint.reference_id
            state.persisted = True
            logger.info(
                "Workflow run %s persisted complaint %s (outcome=%s)",
                state.workflow_run_id,
                complaint.reference_id,
                state.outcome.value if state.outcome else "unknown",
            )

    @staticmethod
    def _existing_complaint_id(session: Session, workflow_run_id: str) -> uuid.UUID | None:
        """Idempotency guard: a run id that already wrote a complaint is reused."""
        row = session.execute(
            select(AgentExecutionLog.complaint_id)
            .where(
                AgentExecutionLog.workflow_run_id == workflow_run_id,
                AgentExecutionLog.complaint_id.is_not(None),
            )
            .limit(1)
        ).first()
        return row[0] if row else None

    @staticmethod
    def _append_workflow_step_logs(
        session: Session, complaint: Complaint, state: WorkflowState
    ) -> None:
        """Record the routing and resolution steps, which persistence does not log.

        Only the consequential *advisory* steps are logged; skipped nodes stay in
        the response trace. No assignment, status-history or audit row is written
        here or anywhere else in the workflow.
        """
        last_index = (
            session.execute(
                select(AgentExecutionLog.step_index)
                .where(AgentExecutionLog.complaint_id == complaint.id)
                .order_by(AgentExecutionLog.step_index.desc())
                .limit(1)
            ).scalar_one_or_none()
            or 0
        )

        steps: list[tuple[AgentName, Any, float | None, str]] = []
        if state.routing is not None:
            steps.append(
                (
                    AgentName.DEPARTMENT_ROUTING,
                    state.routing,
                    state.node_timings_ms.get(NODE_ROUTING),
                    state.routing.rationale,
                )
            )
        if state.resolution is not None:
            steps.append(
                (
                    AgentName.RESOLUTION_RECOMMENDATION,
                    state.resolution,
                    state.node_timings_ms.get(NODE_RESOLUTION),
                    state.resolution.rationale,
                )
            )

        for offset, (agent, recommendation, duration_ms, explanation) in enumerate(steps, start=1):
            status = (
                AgentExecutionStatus.AWAITING_HUMAN
                if getattr(recommendation, "requires_human_review", False)
                else AgentExecutionStatus.SUCCEEDED
            )
            session.add(
                AgentExecutionLog(
                    complaint_id=complaint.id,
                    workflow_run_id=state.workflow_run_id,
                    agent_name=agent,
                    step_index=last_index + offset,
                    status=status,
                    input_payload={
                        "category": (
                            recommendation.category.value if recommendation.category else None
                        )
                    },
                    output_payload=recommendation.model_dump(mode="json"),
                    explanation=explanation,
                    confidence=recommendation.confidence,
                    duration_ms=int(duration_ms) if duration_ms else None,
                )
            )

    def _summarise(
        self, state: WorkflowState, *, persisted: bool, interrupted: bool
    ) -> WorkflowRunResult:
        total = round(sum(state.node_timings_ms.values()), 1)
        outcome = state.outcome or WorkflowOutcome.AWAITING_HUMAN_REVIEW
        return WorkflowRunResult(
            workflow_run_id=state.workflow_run_id,
            request_id=state.request_id,
            outcome=outcome,
            complaint_id=state.complaint_id,
            reference_id=state.reference_id,
            intake=state.intake,
            classification=state.classification,
            duplicate=state.duplicate,
            duplicate_matches=list(state.duplicate.matches) if state.duplicate else [],
            evidence=state.evidence,
            routing=state.routing,
            resolution=state.resolution,
            review_required=state.needs_human_review,
            review_reasons=state.review_reasons,
            human_review_completed=state.human_review_completed,
            errors=state.errors,
            degraded=state.degraded,
            degraded_reasons=state.degraded_reasons,
            nodes=state.trace,
            node_latencies_ms=dict(state.node_timings_ms),
            total_latency_ms=total,
            persisted=persisted,
        )


@lru_cache(maxsize=1)
def get_workflow() -> ComplaintWorkflow:
    """Process-wide workflow so the checkpointer and agent state are shared."""
    settings = default_settings
    checkpointer = build_checkpointer() if settings.orchestration_checkpointing_enabled else None
    return ComplaintWorkflow(
        deps=WorkflowDependencies.build(settings=settings), checkpointer=checkpointer
    )


__all__ = [
    "NODE_ASSEMBLE",
    "NODE_CLASSIFICATION",
    "NODE_DUPLICATE",
    "NODE_EVIDENCE",
    "NODE_HUMAN_REVIEW",
    "NODE_INTAKE",
    "NODE_RESOLUTION",
    "NODE_ROUTING",
    "ComplaintWorkflow",
    "WorkflowDependencies",
    "WorkflowInputs",
    "build_checkpointer",
    "build_workflow_graph",
    "get_workflow",
    "route_after_classification",
    "route_after_duplicate",
    "route_after_intake",
    "route_after_resolution",
    "route_after_routing",
]
