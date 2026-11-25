"""Phase 6 unit and integration tests: the LangGraph orchestration workflow.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase6_workflow.py -v

Four tiers, each independently skippable:

* **Deterministic graph tests** - the agents are built with ``use_llm=False`` (or
  a scripted fake model), so the whole graph runs with no model and no database.
* **Reliability tests** - the timeout/retry primitive and the degraded paths,
  including a scripted model that is unavailable or returns unusable output.
* **Endpoint and database tests** - the ``/api/v1/workflow`` endpoints, the
  persistence/idempotency and audit-integrity guarantees, and the resume
  authorisation checks; skipped without PostgreSQL or its seeded users.
* **Live tests** - one end-to-end run against the real ``llama3.1:latest``
  model; skipped when Ollama or the configured model is missing.

The tests assert the phase's structural safety properties: every node is timed
and recorded, a failed node degrades the run instead of inventing a result, the
workflow never performs a consequential action on its own, and a human-review
interrupt records an acknowledgement rather than an authorisation.
"""

from __future__ import annotations

import base64
import json
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.duplicate import ComplaintSnapshot
from app.agents.errors import AgentLlmError
from app.agents.evidence import EvidenceImage
from app.agents.llm_client import LlmCallResult, LlmClient
from app.agents.pipeline import ComplaintPipeline
from app.agents.workflow import (
    NODE_ASSEMBLE,
    NODE_CLASSIFICATION,
    NODE_DUPLICATE,
    NODE_EVIDENCE,
    NODE_HUMAN_REVIEW,
    NODE_INTAKE,
    NODE_RESOLUTION,
    NODE_ROUTING,
    ComplaintWorkflow,
    WorkflowDependencies,
    WorkflowInputs,
    _execute,
    build_checkpointer,
    get_workflow,
)
from app.core.config import settings
from app.database.session import check_database, get_engine, get_session_factory
from app.main import app
from app.models.agent_execution_log import AgentExecutionLog
from app.models.complaint import Complaint
from app.models.enums import (
    AgentExecutionStatus,
    AgentName,
    ComplaintCategory,
    ComplaintPriority,
    ComplaintStatus,
)
from app.models.status_history import ComplaintStatusHistory
from app.schemas.workflow import WorkflowOutcome, WorkflowRunResult
from app.services.ollama_service import check_ollama_service, find_model

GARBAGE = "Garbage has not been collected on our street for five days."
GARBAGE_NO_LOCATION = "Garbage has not been collected for five days."
THIN = "Fix it"
UNDETERMINED = "Something is wrong in our area please fix it soon"
ANNA_NAGAR = (13.0850, 80.2100)

FULL_PATH = [
    NODE_INTAKE,
    NODE_CLASSIFICATION,
    NODE_DUPLICATE,
    NODE_EVIDENCE,
    NODE_ROUTING,
    NODE_RESOLUTION,
    NODE_ASSEMBLE,
]


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------

_ollama_status = check_ollama_service()
_ollama_model = (
    find_model(_ollama_status, settings.ollama_model) if _ollama_status.reachable else None
)
requires_ollama = pytest.mark.skipif(
    _ollama_model is None,
    reason=f"Ollama model {settings.ollama_model!r} unavailable; live workflow test skipped",
)

_database_status = check_database()
requires_database = pytest.mark.skipif(
    not _database_status.connected,
    reason="PostgreSQL not reachable; run `python -m app.cli health` first",
)


def _user_id(role: str) -> uuid.UUID | None:
    if not _database_status.connected:
        return None
    try:
        with get_engine().connect() as connection:
            return connection.execute(
                text("SELECT id FROM users WHERE role = :role LIMIT 1"), {"role": role}
            ).scalar_one_or_none()
    except Exception:
        return None


_SEEDED_OFFICER = _user_id("officer")
_SEEDED_CITIZEN = _user_id("citizen")

requires_officer = pytest.mark.skipif(
    _SEEDED_OFFICER is None,
    reason="no officer stored; the resume-authorisation check needs one",
)
requires_citizen = pytest.mark.skipif(
    _SEEDED_CITIZEN is None,
    reason="no citizen stored; the unauthorized-resume check needs one",
)


# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


class ScriptedLlm:
    """Fake ``LlmClient`` returning pre-scripted responses in order."""

    def __init__(self, responses: list[str] | None = None, *, available: bool = True) -> None:
        self.responses = list(responses or [])
        self.available = available
        self.calls: list[dict[str, str]] = []

    def is_available(self) -> bool:
        return self.available

    def complete(self, *, system: str, user: str) -> LlmCallResult:
        self.calls.append({"system": system, "user": user})
        if not self.responses:
            raise AgentLlmError("no scripted response left")
        return LlmCallResult(
            text=self.responses.pop(0),
            model="scripted-fake",
            provider="fake",
            latency_ms=1.0,
        )


def _intake_json() -> str:
    return json.dumps(
        {
            "normalized_description": "Garbage has not been collected for five days.",
            "problem_type": "garbage_not_collected",
            "duration_days": 5,
            "duration_text": "five days",
            "location_text": None,
            "landmarks": [],
            "missing_information": ["exact_location"],
            "clarification_questions": ["Which street is affected?"],
            "confidence": 0.8,
            "reasoning": "The citizen reports five days of missed collection.",
        }
    )


def _classification_json(
    *,
    category: str = "waste_management",
    priority: str = "high",
    urgency_score: float = 0.6,
    confidence: float = 0.85,
) -> str:
    return json.dumps(
        {
            "category": category,
            "subcategory": "garbage_not_collected",
            "priority": priority,
            "urgency_score": urgency_score,
            "confidence": confidence,
            "reasoning": "The complaint states garbage has not been collected for five days.",
            "recommended_next_action": "Dispatch the collection beat to the reported street.",
        }
    )


def _png_base64(size: int = 320) -> str:
    """A sharp, high-contrast checkerboard, encoded as base64 PNG."""
    checker = (np.indices((size, size)).sum(axis=0) // 8) % 2
    grey = np.where(checker == 1, 235, 20).astype(np.uint8)
    image = np.repeat(grey[:, :, None], 3, axis=2)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _workflow(
    *,
    pipeline: ComplaintPipeline | None = None,
    llm: LlmClient | None = None,
    use_llm: bool = False,
) -> ComplaintWorkflow:
    """A workflow whose agents are deterministic (no model unless asked)."""
    built = pipeline or ComplaintPipeline.build(settings=settings, llm=llm, use_llm=use_llm)
    deps = WorkflowDependencies.build(settings=settings, pipeline=built)
    return ComplaintWorkflow(deps=deps, checkpointer=build_checkpointer())


def _nodes(result) -> list[str]:
    return [entry.node for entry in result.nodes]


def _purge(*complaint_ids: uuid.UUID) -> None:
    """Remove test complaints and every row that references them."""
    session = get_session_factory()()
    try:
        ids = list(complaint_ids)
        for model in (ComplaintStatusHistory, AgentExecutionLog):
            session.execute(delete(model).where(model.complaint_id.in_(ids)))
        session.execute(update(Complaint).where(Complaint.id.in_(ids)).values(duplicate_of_id=None))
        session.execute(delete(Complaint).where(Complaint.id.in_(ids)))
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def client() -> TestClient:
    """The API endpoints; a deterministic workflow is injected per test."""
    return TestClient(app)


@pytest.fixture()
def deterministic_workflow() -> ComplaintWorkflow:
    return _workflow(use_llm=False)


# ---------------------------------------------------------------------------
# 1. The successful end-to-end workflow
# ---------------------------------------------------------------------------


def test_successful_end_to_end_workflow() -> None:
    workflow = _workflow(use_llm=False)

    result = workflow.invoke(
        text=GARBAGE,
        latitude=ANNA_NAGAR[0],
        longitude=ANNA_NAGAR[1],
    )

    assert result.outcome is WorkflowOutcome.COMPLETED
    assert result.intake is not None
    assert result.classification.category is ComplaintCategory.WASTE_MANAGEMENT
    assert result.classification.department_code == "SWM"
    assert result.classification.needs_human_review is False
    assert result.routing is not None
    assert result.routing.recommended_department_code == "SWM"
    assert result.routing.requires_human_review is False
    assert result.resolution is not None
    assert result.resolution.recommendation_only is True
    assert result.resolution.status_change_applied is False

    # Every node ran, in order, and every node was timed.
    assert _nodes(result) == FULL_PATH
    assert set(result.node_latencies_ms) == set(FULL_PATH)
    assert all(value >= 0.0 for value in result.node_latencies_ms.values())
    assert result.total_latency_ms >= 0.0

    # Skipped steps say why; nothing failed and nothing degraded.
    skipped = {entry.node: entry for entry in result.nodes if entry.skipped}
    assert set(skipped) == {NODE_DUPLICATE, NODE_EVIDENCE}
    assert all(entry.skipped_reason for entry in skipped.values())
    assert result.errors == []
    assert result.degraded is False
    assert result.review_required is False

    # The safety contract is returned with every result.
    assert result.requires_human_approval is True
    assert result.automatic_consequential_actions is False
    assert result.persisted is False


def test_missing_location_is_flagged_for_human_review() -> None:
    workflow = _workflow(use_llm=False)

    result = workflow.invoke(text=GARBAGE_NO_LOCATION)

    assert result.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
    assert result.review_required is True
    assert result.routing is not None
    assert result.routing.recommended_department_code == "SWM"
    assert result.routing.requires_human_review is True
    assert "ward_or_location" in result.routing.missing_information
    assert any("jurisdiction" in reason for reason in result.review_reasons)
    # Routing still runs (it names the likely department) but its missing-location
    # flag forces the closing human checkpoint before the result is assembled.
    assert _nodes(result) == [
        NODE_INTAKE,
        NODE_CLASSIFICATION,
        NODE_DUPLICATE,
        NODE_EVIDENCE,
        NODE_ROUTING,
        NODE_RESOLUTION,
        NODE_HUMAN_REVIEW,
        NODE_ASSEMBLE,
    ]


# ---------------------------------------------------------------------------
# 2. Conditional edges: thin, undetermined and fail states
# ---------------------------------------------------------------------------


def test_low_confidence_classification_needs_a_human() -> None:
    workflow = _workflow(use_llm=False)

    result = workflow.invoke(text=UNDETERMINED, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])

    assert result.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
    assert result.classification is not None
    assert result.classification.category is None
    assert result.classification.needs_human_review is True
    assert result.review_required is True
    # The graph stopped at the human checkpoint instead of forcing routing.
    assert result.routing is None
    assert result.resolution is None
    assert _nodes(result) == [
        NODE_INTAKE,
        NODE_CLASSIFICATION,
        NODE_HUMAN_REVIEW,
        NODE_ASSEMBLE,
    ]


def test_too_thin_complaint_goes_straight_to_human_review() -> None:
    workflow = _workflow(use_llm=False)

    result = workflow.invoke(text=THIN, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])

    assert result.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
    assert result.intake is not None
    assert "description_detail" in result.intake.missing_information
    # A complaint too thin to classify is never sent to classification.
    assert result.classification is None
    assert result.routing is None
    assert _nodes(result) == [NODE_INTAKE, NODE_HUMAN_REVIEW, NODE_ASSEMBLE]
    assert any("thin" in reason for reason in result.review_reasons)


def test_empty_complaint_fails_without_running_the_agents() -> None:
    workflow = _workflow(use_llm=False)

    result = workflow.invoke(text="   ")

    assert result.outcome is WorkflowOutcome.FAILED
    assert result.intake is None
    assert result.classification is None
    assert result.errors
    assert result.degraded is True
    assert _nodes(result) == [NODE_INTAKE, NODE_ASSEMBLE]


# ---------------------------------------------------------------------------
# 3. The priority-escalation safeguard survives orchestration
# ---------------------------------------------------------------------------


def test_critical_model_escalation_is_capped_and_reviewed() -> None:
    fake = ScriptedLlm(
        [_intake_json(), _classification_json(priority="critical", urgency_score=0.95)]
    )
    workflow = _workflow(llm=fake, use_llm=True)

    result = workflow.invoke(text=GARBAGE, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])

    assert result.classification is not None
    # The model asked for critical; the evidence did not support it, so the
    # applied band is capped at the ceiling and a human is asked to look.
    assert result.classification.model_priority is ComplaintPriority.CRITICAL
    assert result.classification.priority is ComplaintPriority.HIGH
    assert "capped" in (result.classification.escalation_reason or "")
    assert result.classification.needs_human_review is True
    assert result.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
    assert result.review_required is True


# ---------------------------------------------------------------------------
# 4. Duplicate detection and optional evidence
# ---------------------------------------------------------------------------


def test_potential_duplicate_goes_to_human_review() -> None:
    workflow = _workflow(use_llm=False)
    candidate = ComplaintSnapshot(
        text=GARBAGE,
        reference_id="CB-twin",
        category=ComplaintCategory.WASTE_MANAGEMENT,
        latitude=ANNA_NAGAR[0],
        longitude=ANNA_NAGAR[1],
    )

    result = workflow.invoke(
        text=GARBAGE,
        inputs=WorkflowInputs(candidates=[candidate]),
        latitude=ANNA_NAGAR[0],
        longitude=ANNA_NAGAR[1],
    )

    assert result.duplicate is not None
    assert result.duplicate.likely_duplicate_found is True
    # Detection never merges; a human must confirm any link.
    assert result.duplicate.linking_requires_human_approval is True
    assert result.duplicate_matches
    assert result.duplicate_matches[0].reference_id == "CB-twin"
    assert result.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
    # The graph stopped at the human checkpoint instead of going on to routing.
    assert result.routing is None
    assert NODE_HUMAN_REVIEW in _nodes(result)


def test_evidence_is_optional_and_analysed_when_supplied() -> None:
    workflow = _workflow(use_llm=False)
    image = EvidenceImage.from_bytes("street.png", base64.b64decode(_png_base64()))

    with_image = workflow.invoke(
        text=GARBAGE,
        inputs=WorkflowInputs(images=[image]),
        latitude=ANNA_NAGAR[0],
        longitude=ANNA_NAGAR[1],
    )
    assert len(with_image.evidence) == 1
    assert with_image.evidence[0].image_analyzed is True
    evidence_entry = next(entry for entry in with_image.nodes if entry.node == NODE_EVIDENCE)
    assert evidence_entry.skipped is False

    without_image = workflow.invoke(text=GARBAGE, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])
    assert without_image.evidence == []
    skipped_entry = next(entry for entry in without_image.nodes if entry.node == NODE_EVIDENCE)
    assert skipped_entry.skipped is True
    assert skipped_entry.skipped_reason


# ---------------------------------------------------------------------------
# 5. Unavailable model and malformed output degrade, never crash
# ---------------------------------------------------------------------------


def test_unavailable_model_degrades_but_the_workflow_completes() -> None:
    fake = ScriptedLlm([], available=False)
    workflow = _workflow(llm=fake, use_llm=True)

    result = workflow.invoke(text=GARBAGE, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])

    assert fake.calls == []
    assert result.outcome is WorkflowOutcome.DEGRADED
    assert result.degraded is True
    assert result.degraded_reasons
    assert result.classification is not None
    assert result.classification.degraded is True
    # The deterministic fallback still produces the correct category.
    assert result.classification.category is ComplaintCategory.WASTE_MANAGEMENT
    assert result.routing is not None


def test_malformed_agent_output_is_handled() -> None:
    fake = ScriptedLlm(["this is not JSON at all", "still not JSON"])
    workflow = _workflow(llm=fake, use_llm=True)

    result = workflow.invoke(text=GARBAGE, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])

    assert result.outcome is WorkflowOutcome.DEGRADED
    assert result.classification is not None
    assert result.classification.degraded is True
    assert result.classification.category is ComplaintCategory.WASTE_MANAGEMENT
    # The agents repaired-and-fell-back internally, so no node failed outright.
    assert result.errors == []
    assert all(entry.status is not AgentExecutionStatus.FAILED for entry in result.nodes)


def test_a_failed_node_degrades_the_run_instead_of_inventing_a_result() -> None:
    pipeline = ComplaintPipeline.build(settings=settings, use_llm=False)

    class _ExplodingRouting:
        """Stands in for the routing agent: a transient agent failure."""

        def run(self, _payload: object) -> object:
            raise AgentLlmError("the routing agent is unavailable")

    deps = WorkflowDependencies.build(settings=settings, pipeline=pipeline)
    deps = WorkflowDependencies(
        pipeline=deps.pipeline,
        routing=_ExplodingRouting(),
        resolution=deps.resolution,
        settings=settings,
    )
    workflow = ComplaintWorkflow(deps=deps, checkpointer=build_checkpointer())

    result = workflow.invoke(text=GARBAGE, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])

    assert result.routing is None
    assert result.degraded is True
    assert result.errors
    routing_entry = next(entry for entry in result.nodes if entry.node == NODE_ROUTING)
    assert routing_entry.status is AgentExecutionStatus.FAILED
    assert routing_entry.attempt == settings.orchestration_max_node_attempts
    assert routing_entry.error
    assert result.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW


# ---------------------------------------------------------------------------
# 6. Timeouts, bounded retries and idempotent retries
# ---------------------------------------------------------------------------


def test_transient_error_is_retried_then_succeeds() -> None:
    config = settings.model_copy(
        update={"orchestration_max_node_attempts": 3, "orchestration_node_timeout_seconds": 0}
    )
    calls = {"count": 0}

    def flaky() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise AgentLlmError("transient transport error")
        return "ok"

    outcome = _execute(flaky, settings=config)

    assert outcome.status is AgentExecutionStatus.SUCCEEDED
    assert outcome.attempt == 2
    assert outcome.result == "ok"
    assert outcome.degraded is False
    assert calls["count"] == 2


def test_non_transient_error_is_not_retried() -> None:
    config = settings.model_copy(
        update={"orchestration_max_node_attempts": 3, "orchestration_node_timeout_seconds": 0}
    )
    calls = {"count": 0}

    def buggy() -> str:
        calls["count"] += 1
        raise ValueError("a programming error, not a transient one")

    outcome = _execute(buggy, settings=config)

    assert outcome.status is AgentExecutionStatus.FAILED
    assert outcome.attempt == 1
    assert calls["count"] == 1
    assert outcome.degraded is True
    # Internal detail is not leaked into the trace.
    assert "unexpected workflow error" in (outcome.error or "")


def test_retries_are_bounded_by_the_configured_ceiling() -> None:
    config = settings.model_copy(
        update={"orchestration_max_node_attempts": 2, "orchestration_node_timeout_seconds": 0}
    )
    calls = {"count": 0}

    def always_fails() -> str:
        calls["count"] += 1
        raise AgentLlmError("still down")

    outcome = _execute(always_fails, settings=config)

    assert outcome.status is AgentExecutionStatus.FAILED
    assert outcome.attempt == 2
    assert calls["count"] == 2
    assert outcome.degraded is True


def test_retry_of_a_successful_node_cannot_duplicate_a_side_effect() -> None:
    config = settings.model_copy(
        update={"orchestration_max_node_attempts": 3, "orchestration_node_timeout_seconds": 0}
    )
    calls = {"count": 0}

    def once() -> str:
        calls["count"] += 1
        return "written"

    outcome = _execute(once, settings=config)

    assert outcome.status is AgentExecutionStatus.SUCCEEDED
    assert outcome.attempt == 1
    # A success returns on the first attempt, so no other write can happen.
    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# 7. Human-review interruption and resumption
# ---------------------------------------------------------------------------


def test_interrupt_and_resume_records_an_acknowledgement() -> None:
    workflow = _workflow(use_llm=False)
    run_id = f"wf-interrupt-{uuid.uuid4().hex[:8]}"

    paused = workflow.invoke(
        text=GARBAGE_NO_LOCATION,
        inputs=WorkflowInputs(interrupt_for_review=True),
        workflow_run_id=run_id,
    )

    assert paused.workflow_run_id == run_id
    assert paused.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
    assert paused.review_required is True
    assert paused.human_review_completed is False

    resumed = workflow.resume(
        workflow_run_id=run_id, reviewer_id=str(uuid.uuid4()), note="looked at the complaint"
    )

    assert resumed.human_review_completed is True
    assert resumed.review_required is True
    assert resumed.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
    # An acknowledgement is not an authorisation.
    assert resumed.requires_human_approval is True
    assert resumed.automatic_consequential_actions is False


def test_resume_of_a_run_that_never_paused_is_safe() -> None:
    workflow = _workflow(use_llm=False)
    run_id = f"wf-nointerrupt-{uuid.uuid4().hex[:8]}"

    completed = workflow.invoke(
        text=GARBAGE,
        inputs=WorkflowInputs(interrupt_for_review=False),
        workflow_run_id=run_id,
        latitude=ANNA_NAGAR[0],
        longitude=ANNA_NAGAR[1],
    )
    assert completed.outcome is WorkflowOutcome.COMPLETED

    # Resuming a thread whose graph already reached END simply replays the
    # finished state; it never re-runs the agents or changes the outcome.
    resumed = workflow.resume(workflow_run_id=run_id, reviewer_id=str(uuid.uuid4()))

    assert resumed.outcome is WorkflowOutcome.COMPLETED
    assert resumed.automatic_consequential_actions is False


# ---------------------------------------------------------------------------
# 8. The versioned orchestration endpoints (offline)
# ---------------------------------------------------------------------------


def test_run_endpoint_returns_the_assembled_result(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    deterministic_workflow: ComplaintWorkflow,
) -> None:
    monkeypatch.setattr("app.api.workflow.get_workflow", lambda: deterministic_workflow)

    response = client.post(
        "/api/v1/workflow/run",
        json={"text": GARBAGE, "latitude": ANNA_NAGAR[0], "longitude": ANNA_NAGAR[1]},
    )

    assert response.status_code == 200
    body = response.json()
    assert WorkflowRunResult.model_validate(body)
    assert body["outcome"] == WorkflowOutcome.COMPLETED.value
    assert body["routing"]["recommended_department_code"] == "SWM"
    assert body["resolution"]["recommendation_only"] is True
    assert body["review_required"] is False
    assert body["duplicate_matches"] == []
    assert body["requires_human_approval"] is True
    assert body["automatic_consequential_actions"] is False
    assert body["errors"] == []
    assert body["degraded"] is False
    assert body["total_latency_ms"] >= 0
    assert [node["node"] for node in body["nodes"]] == FULL_PATH


def test_run_endpoint_reports_potential_duplicates(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    deterministic_workflow: ComplaintWorkflow,
) -> None:
    monkeypatch.setattr("app.api.workflow.get_workflow", lambda: deterministic_workflow)

    response = client.post(
        "/api/v1/workflow/run",
        json={
            "text": GARBAGE,
            "latitude": ANNA_NAGAR[0],
            "longitude": ANNA_NAGAR[1],
            "candidates": [{"text": GARBAGE, "reference_id": "CB-twin"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == WorkflowOutcome.AWAITING_HUMAN_REVIEW.value
    assert body["review_required"] is True
    assert body["duplicate_matches"]
    assert body["duplicate_matches"][0]["reference_id"] == "CB-twin"
    # A potential duplicate is surfaced to a human, never merged automatically.
    assert body["automatic_consequential_actions"] is False


# ---------------------------------------------------------------------------
# 9. Database persistence and audit integrity (database)
# ---------------------------------------------------------------------------


@requires_database
def test_run_endpoint_persists_once_and_never_writes_the_audit_trail(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    deterministic_workflow: ComplaintWorkflow,
) -> None:
    monkeypatch.setattr("app.api.workflow.get_workflow", lambda: deterministic_workflow)
    run_id = f"wf-persist-{uuid.uuid4().hex[:10]}"
    payload = {
        "text": GARBAGE,
        "request_id": run_id,
        "latitude": ANNA_NAGAR[0],
        "longitude": ANNA_NAGAR[1],
        "persist": True,
    }

    first = client.post("/api/v1/workflow/run", json=payload)
    assert first.status_code == 200
    body = first.json()
    assert body["persisted"] is True
    complaint_id = uuid.UUID(body["complaint_id"])

    try:
        # A replayed request with the same run id is idempotent: no second row.
        second = client.post("/api/v1/workflow/run", json=payload)
        assert second.status_code == 200
        assert second.json()["complaint_id"] == body["complaint_id"]

        session = get_session_factory()()
        try:
            rows = list(
                session.execute(select(Complaint).where(Complaint.id == complaint_id)).scalars()
            )
            assert len(rows) == 1
            complaint = rows[0]
            # Advisory only: the complaint is created at SUBMITTED with the
            # *suggested* department recorded (the pre-existing Phase 3
            # persistence behaviour), but nothing is assigned, merged, resolved
            # or closed, and no audit row is written (asserted below).
            assert complaint.status is ComplaintStatus.SUBMITTED
            assert complaint.duplicate_of_id is None
            assert complaint.resolved_at is None
            assert complaint.closed_at is None

            logs = list(
                session.execute(
                    select(AgentExecutionLog).where(AgentExecutionLog.workflow_run_id == run_id)
                ).scalars()
            )
            assert logs
            assert {log.complaint_id for log in logs} == {complaint_id}
            agents = {log.agent_name for log in logs}
            assert AgentName.INTAKE in agents
            assert AgentName.CLASSIFICATION in agents
            assert AgentName.DEPARTMENT_ROUTING in agents
            assert AgentName.RESOLUTION_RECOMMENDATION in agents

            history = list(
                session.execute(
                    select(ComplaintStatusHistory).where(
                        ComplaintStatusHistory.complaint_id == complaint_id
                    )
                ).scalars()
            )
            # The advisory agents never write the audit trail; only an officer does.
            assert history == []
        finally:
            session.close()
    finally:
        _purge(complaint_id)


# ---------------------------------------------------------------------------
# 10. Resume authorisation (database)
# ---------------------------------------------------------------------------


@requires_database
@requires_citizen
def test_a_citizen_cannot_resume_a_review_checkpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    deterministic_workflow: ComplaintWorkflow,
) -> None:
    monkeypatch.setattr("app.api.workflow.get_workflow", lambda: deterministic_workflow)

    response = client.post(
        "/api/v1/workflow/wf-unused/resume",
        json={"reviewer_id": str(_SEEDED_CITIZEN)},
    )

    assert response.status_code == 403
    assert "officer" in response.json()["detail"]


@requires_database
def test_an_unknown_reviewer_cannot_resume_a_review_checkpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    deterministic_workflow: ComplaintWorkflow,
) -> None:
    monkeypatch.setattr("app.api.workflow.get_workflow", lambda: deterministic_workflow)

    response = client.post(
        "/api/v1/workflow/wf-unused/resume",
        json={"reviewer_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404


@requires_database
@requires_officer
def test_an_officer_resume_records_an_acknowledgement_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(use_llm=False)
    run_id = f"wf-officer-{uuid.uuid4().hex[:8]}"

    paused = workflow.invoke(
        text=GARBAGE_NO_LOCATION,
        inputs=WorkflowInputs(interrupt_for_review=True),
        workflow_run_id=run_id,
    )
    assert paused.human_review_completed is False
    assert paused.review_required is True

    monkeypatch.setattr("app.api.workflow.get_workflow", lambda: workflow)
    response = client.post(
        f"/api/v1/workflow/{run_id}/resume",
        json={"reviewer_id": str(_SEEDED_OFFICER), "note": "reviewed the checkpoint"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["human_review_completed"] is True
    # An acknowledgement is not an authorisation.
    assert body["requires_human_approval"] is True
    assert body["automatic_consequential_actions"] is False


# ---------------------------------------------------------------------------
# 11. Live inference against the local Ollama model
# ---------------------------------------------------------------------------


@requires_ollama
def test_live_workflow_end_to_end_against_local_model() -> None:
    result = get_workflow().invoke(text=GARBAGE, latitude=ANNA_NAGAR[0], longitude=ANNA_NAGAR[1])

    assert result.outcome in {
        WorkflowOutcome.COMPLETED,
        WorkflowOutcome.AWAITING_HUMAN_REVIEW,
        WorkflowOutcome.DEGRADED,
    }
    assert result.classification is not None
    assert result.total_latency_ms > 0
    assert result.automatic_consequential_actions is False
    # The live model may legitimately stop at the human checkpoint when it is
    # not confident. When routing did run it must name a department; when it did
    # not, the run must be waiting on a reviewer.
    if result.routing is not None:
        assert result.routing.recommended_department_code
    else:
        assert result.outcome is WorkflowOutcome.AWAITING_HUMAN_REVIEW
        assert result.review_required is True


@requires_ollama
def test_live_workflow_never_exceeds_the_priority_ceiling_without_evidence() -> None:
    result = get_workflow().invoke(text=GARBAGE)

    assert result.classification is not None
    if result.classification.model_priority is ComplaintPriority.CRITICAL:
        # A model-only critical claim must never be applied without evidence.
        assert result.classification.priority is not ComplaintPriority.CRITICAL
        assert result.classification.needs_human_review is True
