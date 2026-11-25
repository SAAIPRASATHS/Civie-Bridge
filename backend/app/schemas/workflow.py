"""Typed state and result models for the Phase 6 LangGraph orchestration.

The graph in :mod:`app.agents.workflow` threads one :class:`WorkflowState`
through every node. It is a validated Pydantic model rather than a loose dict so
that a malformed node update fails loudly at the boundary instead of silently
corrupting the run.

Three precautions are structural here:

* The state carries identifiers, agent *results* and metadata - never raw image
  bytes, model prompts or credentials. Runtime-only inputs (images, candidate
  complaints to compare against) are passed through the graph config, so they
  never enter a serialised checkpoint.
* ``WorkflowOutcome`` has no value meaning "actioned": a run can complete, wait
  for a human, degrade or fail, but it can never be recorded as having assigned,
  merged, resolved or closed a complaint. Nothing in this workflow performs those
  actions - they remain the exclusive job of the Phase 4/5 officer endpoints.
* Every node records a :class:`NodeTrace` with a *safe* error summary so the
  observability record is useful without leaking model internals or citizen PII.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentExecutionStatus
from app.schemas.agent import (
    ANALYSIS_PROVENANCE,
    ClassificationResult,
    DuplicateCandidate,
    DuplicateDetectionResult,
    EvidenceAnalysisResult,
    IntakeResult,
    ResolutionRecommendation,
    RoutingRecommendation,
)


class WorkflowOutcome(enum.StrEnum):
    """Terminal status of one orchestration run.

    Intentionally action-free: none of these values asserts that a complaint was
    assigned, linked, resolved, rejected or closed.
    """

    COMPLETED = "completed"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    DEGRADED = "degraded"
    FAILED = "failed"


class NodeTrace(BaseModel):
    """One recorded node execution: what ran, how long, and how it ended."""

    model_config = ConfigDict(extra="forbid")

    node: str = Field(description="Stable node name, e.g. 'classification'.")
    status: AgentExecutionStatus
    attempt: int = Field(default=1, ge=1, description="1 for the first try, higher after a retry.")
    duration_ms: float = Field(ge=0.0)
    skipped: bool = False
    skipped_reason: str | None = None
    error: str | None = Field(
        default=None, description="A short, safe summary; never a full traceback or secret."
    )
    degraded: bool = Field(
        default=False, description="True when a deterministic fallback replaced a failed step."
    )


class WorkflowState(BaseModel):
    """The single object threaded through every graph node.

    Fields are grouped by lifecycle stage. A node returns only the fields it
    changed; the workflow appends to the trace by returning the full list so the
    behaviour does not depend on a channel reducer.
    """

    model_config = ConfigDict(extra="forbid")

    # -- Identity ----------------------------------------------------------
    workflow_run_id: str = Field(
        description="Stable id for this run, also the checkpoint thread id."
    )
    request_id: str | None = Field(default=None, description="Caller-supplied idempotency key.")
    complaint_id: str | None = None
    reference_id: str | None = None

    # -- Input -------------------------------------------------------------
    complaint_text: str
    latitude: float | None = None
    longitude: float | None = None

    # -- Agent results -----------------------------------------------------
    intake: IntakeResult | None = None
    classification: ClassificationResult | None = None
    duplicate: DuplicateDetectionResult | None = None
    evidence: list[EvidenceAnalysisResult] = Field(default_factory=list)
    routing: RoutingRecommendation | None = None
    resolution: ResolutionRecommendation | None = None

    # -- Human review ------------------------------------------------------
    needs_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    human_review_completed: bool = False
    reviewer_id: str | None = None
    resume_note: str | None = None

    # -- Errors and degradation -------------------------------------------
    errors: list[str] = Field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)

    # -- Trace and timing --------------------------------------------------
    trace: list[NodeTrace] = Field(default_factory=list)
    node_timings_ms: dict[str, float] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    current_node: str | None = None
    outcome: WorkflowOutcome | None = None
    persisted: bool = False


class WorkflowRunResult(BaseModel):
    """The complete, safe summary of one orchestration run returned to a caller."""

    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    request_id: str | None = None
    outcome: WorkflowOutcome

    complaint_id: str | None = None
    reference_id: str | None = None

    # -- Validated agent outputs ------------------------------------------
    intake: IntakeResult | None = None
    classification: ClassificationResult | None = None
    duplicate: DuplicateDetectionResult | None = None
    duplicate_matches: list[DuplicateCandidate] = Field(
        default_factory=list, description="Ranked potential matches, empty when none were compared."
    )
    evidence: list[EvidenceAnalysisResult] = Field(default_factory=list)
    routing: RoutingRecommendation | None = None
    resolution: ResolutionRecommendation | None = None

    # -- Review requirements ----------------------------------------------
    review_required: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    human_review_completed: bool = False

    # -- Errors / degraded mode -------------------------------------------
    errors: list[str] = Field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)

    # -- Observability -----------------------------------------------------
    nodes: list[NodeTrace] = Field(default_factory=list)
    node_latencies_ms: dict[str, float] = Field(default_factory=dict)
    total_latency_ms: float = Field(ge=0.0)
    persisted: bool = False

    # -- Safety constants --------------------------------------------------
    requires_human_approval: bool = True
    automatic_consequential_actions: bool = Field(
        default=False,
        description="Always false: this workflow assigns, merges, resolves, rejects and closes nothing.",
    )
    provenance: str = ANALYSIS_PROVENANCE
