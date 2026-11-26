"""Phase 7 officer dashboard endpoints.

    GET /api/officer/complaints                          -> OfficerComplaintPage
    GET /api/officer/complaints/{complaint_id}           -> OfficerComplaintDetail
    GET /api/officer/complaints/{complaint_id}/audit     -> OfficerAuditTrail
    GET /api/officer/review-queue                        -> OfficerComplaintPage
    GET /api/officer/departments                         -> DepartmentDirectoryPage

Every route is guarded by :data:`~app.api.deps.OfficerUser`, so the caller is
resolved from the signed session cookie and re-checked against the database
before the handler body runs. A citizen (or an anonymous caller) is refused on
the server regardless of what the browser chooses to render.

The dashboard is **read-only**. It surfaces exactly what the agent pipeline
recorded - the category, the priority, the suggested department, the potential
duplicate matches with their similarity reasons, the evidence analysis, the
pending human-review queue and the audit trail - and it never approves,
rejects, assigns, merges, resolves or closes anything. Those decisions remain
the exclusive job of the explicit Phase 4/5 officer action endpoints:

* ``POST /api/complaints/{id}/duplicates/review``  - the only writer of ``duplicate_of_id``
* ``POST /api/complaints/{id}/routing/decision``   - the only writer of ``current_department_id``
* ``POST /api/v1/workflow/{run_id}/resume``        - an acknowledgement, not an authorisation

Everything returned here is advisory and carries an explicit suggestion notice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.agents.duplicate import ComplaintSnapshot, DuplicateDetectionAgent
from app.agents.pipeline import get_pipeline
from app.agents.resolution import ResolutionAgent, ResolutionInput
from app.agents.routing import RoutingAgent, RoutingInput
from app.api.deps import OfficerUser, SessionDep
from app.core.logging import get_logger
from app.models.agent_execution_log import AgentExecutionLog
from app.models.assignment import ComplaintAssignment
from app.models.complaint import Complaint
from app.models.complaint_evidence import ComplaintEvidence
from app.models.department import Department
from app.models.enums import (
    AgentExecutionStatus,
    AgentName,
    AssignmentApprovalStatus,
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    ComplaintStatus,
    EvidenceVerificationStatus,
)
from app.models.status_history import ComplaintStatusHistory
from app.models.user import User
from app.schemas.agent import (
    ANALYSIS_PROVENANCE,
    ClassificationResult,
    DuplicateDetectionResult,
    EvidenceAnalysisResult,
    IntakeResult,
    ResolutionRecommendation,
    RoutingRecommendation,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/officer", tags=["officer"])

#: Both routing and resolution are deterministic and hold no model client, so
#: one instance each is enough for the whole process.
_ROUTING_AGENT = RoutingAgent()
_RESOLUTION_AGENT = ResolutionAgent()

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

#: Stored complaints read as the duplicate comparison set for one detail view.
CANDIDATE_SCAN_LIMIT = 100

#: Statuses that already reflect a human decision. Anything outside this set is
#: still waiting for an officer and therefore belongs in the review queue.
DECIDED_STATUSES: frozenset[ComplaintStatus] = frozenset(
    {
        ComplaintStatus.ASSIGNED,
        ComplaintStatus.IN_PROGRESS,
        ComplaintStatus.RESOLVED,
        ComplaintStatus.CLOSED,
        ComplaintStatus.REJECTED,
        ComplaintStatus.DUPLICATE,
    }
)

SUGGESTION_NOTICE = (
    "Everything shown here is AI-generated advisory output recorded by the agent "
    "pipeline. Category, priority, suggested department, duplicate matches and "
    "resolution steps are suggestions for a human officer. Nothing has been "
    "approved, rejected, assigned, merged, resolved or closed automatically."
)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class OfficerComplaintSummary(BaseModel):
    """One complaint as it appears in the officer list and queue."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: uuid.UUID
    reference_id: str
    status: ComplaintStatus
    pending_human_review: bool = Field(
        description="True when this complaint still awaits an officer decision."
    )

    category: ComplaintCategory | None = None
    priority: ComplaintPriority | None = None
    language: ComplaintLanguage
    ward: str | None = None
    location_text: str | None = None

    #: AI-suggested / approved department, with the column it came from.
    recommended_department_code: str | None = None
    recommended_department_name: str | None = None
    department_is_approved: bool = Field(
        default=False, description="True when an officer has approved the routing."
    )

    classification_confidence: float | None = None
    urgency_score: float | None = None

    reported_at: datetime | None = None
    submitted_at: datetime | None = None
    sla_due_at: datetime | None = None
    sla_breached: bool = False

    is_duplicate: bool = False
    duplicate_of_reference: str | None = None
    duplicate_similarity: float | None = None
    needs_clarification: bool = False
    evidence_count: int = 0


class OfficerComplaintPage(BaseModel):
    """A page of complaints, newest first."""

    model_config = ConfigDict(extra="forbid")

    total: int
    limit: int
    offset: int
    returned: int
    items: list[OfficerComplaintSummary] = Field(default_factory=list)
    suggestion_notice: str = SUGGESTION_NOTICE


class OfficerCitizenAccount(BaseModel):
    """The complaint's author, limited to what the dashboard needs.

    Deliberately excludes the email address and any credential field: an officer
    does not need a citizen's sign-in identity to triage a complaint.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID | None = None
    display_name: str | None = None
    preferred_language: str | None = None
    is_synthetic: bool = Field(
        description="True for a seeded prototype account, false for a real portal registration."
    )


class OfficerEvidenceItem(BaseModel):
    """Stored evidence metadata joined to its AI analysis, when one exists."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: uuid.UUID
    file_name: str
    mime_type: str | None = None
    file_size_bytes: int | None = None
    caption: str | None = None

    verification_status: EvidenceVerificationStatus
    relevance_score: float | None = None
    requires_manual_review: bool = False
    verification_notes: str | None = None
    agent_explanation: str | None = None
    analysis_model: str | None = None
    uploaded_at: datetime | None = None

    analysis: EvidenceAnalysisResult | None = Field(
        default=None, description="The full analysis record from the agent step, when stored."
    )


class OfficerAssignmentItem(BaseModel):
    """One routing recommendation and its recorded human outcome."""

    model_config = ConfigDict(extra="forbid")

    assignment_id: uuid.UUID
    department_code: str | None = None
    department_name: str | None = None
    officer_id: uuid.UUID | None = None

    ai_recommended_department_code: str | None = None
    ai_confidence: float | None = None
    ai_explanation: str | None = None

    approval_status: AssignmentApprovalStatus
    assigned_by_user_id: uuid.UUID | None = None
    approved_by_user_id: uuid.UUID | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    notes: str | None = None
    created_at: datetime | None = None


class OfficerAgentStep(BaseModel):
    """One recorded agent step, for the dashboard's observability panel."""

    model_config = ConfigDict(extra="forbid")

    agent_name: AgentName
    step_index: int | None = None
    status: AgentExecutionStatus
    explanation: str | None = None
    confidence: float | None = None
    model_name: str | None = None
    duration_ms: int | None = None
    workflow_run_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class OfficerStatusEvent(BaseModel):
    """One append-only status transition from the audit trail."""

    model_config = ConfigDict(extra="forbid")

    history_id: uuid.UUID
    from_status: ComplaintStatus | None = None
    to_status: ComplaintStatus
    changed_by_user_id: uuid.UUID | None = None
    changed_by_agent: str | None = None
    reason: str | None = None
    notes: str | None = None
    changed_at: datetime | None = None


class OfficerAuditTrail(BaseModel):
    """The complaint's full audit history and status information."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: uuid.UUID
    reference_id: str
    current_status: ComplaintStatus
    status_history: list[OfficerStatusEvent] = Field(default_factory=list)
    assignments: list[OfficerAssignmentItem] = Field(default_factory=list)
    agent_steps: list[OfficerAgentStep] = Field(default_factory=list)


class OfficerComplaintDetail(BaseModel):
    """One complaint with every stored agent result, for the officer dashboard."""

    model_config = ConfigDict(extra="forbid")

    complaint: OfficerComplaintSummary
    citizen_account: OfficerCitizenAccount

    original_text: str = Field(description="The citizen's words, preserved verbatim.")
    normalized_text: str | None = None

    intake: IntakeResult | None = None
    classification: ClassificationResult | None = None
    recommended_department: RoutingRecommendation | None = None
    resolution: ResolutionRecommendation | None = None
    duplicate_matches: DuplicateDetectionResult | None = None

    evidence: list[OfficerEvidenceItem] = Field(default_factory=list)
    assignments: list[OfficerAssignmentItem] = Field(default_factory=list)
    status_history: list[OfficerStatusEvent] = Field(default_factory=list)
    agent_steps: list[OfficerAgentStep] = Field(default_factory=list)

    #: Route the officer must call to actually approve or reject a decision.
    decision_endpoints: dict[str, str] = Field(default_factory=dict)

    suggestion_notice: str = SUGGESTION_NOTICE
    provenance: str = ANALYSIS_PROVENANCE


class DepartmentDirectoryItem(BaseModel):
    """One prototype department, for the dashboard's filter and display."""

    model_config = ConfigDict(extra="forbid")

    department_id: uuid.UUID
    code: str
    name: str
    category: ComplaintCategory
    jurisdiction: str | None = None
    is_active: bool
    is_prototype_data: bool = True


class DepartmentDirectoryPage(BaseModel):
    """The controlled routing directory. Never invented - read from the table."""

    model_config = ConfigDict(extra="forbid")

    total: int
    items: list[DepartmentDirectoryItem] = Field(default_factory=list)
    provenance: str = ANALYSIS_PROVENANCE


class AnalyticsBucket(BaseModel):
    """One labelled count in an analytics breakdown."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    count: int


class AnalyticsTrendPoint(BaseModel):
    """One day's count in a time series."""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(description="The day, as an ISO-8601 date.")
    count: int


class OfficerAnalytics(BaseModel):
    """Aggregate workload and outcome figures for the dashboard charts.

    Every figure is a straight count over the stored complaints; none of them is
    a verified government statistic and the prototype provenance notice is
    returned alongside them.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    window_days: int = Field(description="The trend window, in days.")
    total_complaints: int
    pending_review: int = Field(description="Complaints still awaiting an officer decision.")
    decided: int = Field(description="Complaints already carrying a human decision.")
    sla_breached: int
    awaiting_triage: int = Field(
        description="Complaints with no recorded category; a human must triage them."
    )
    unassigned: int = Field(description="Complaints with no approved department.")
    by_status: list[AnalyticsBucket] = Field(default_factory=list)
    by_category: list[AnalyticsBucket] = Field(default_factory=list)
    by_priority: list[AnalyticsBucket] = Field(default_factory=list)
    by_language: list[AnalyticsBucket] = Field(default_factory=list)
    by_department: list[AnalyticsBucket] = Field(default_factory=list)
    submitted_trend: list[AnalyticsTrendPoint] = Field(default_factory=list)
    resolved_trend: list[AnalyticsTrendPoint] = Field(default_factory=list)
    provenance: str = ANALYSIS_PROVENANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _database_unavailable(exc: SQLAlchemyError) -> HTTPException:
    logger.warning("Officer endpoint database error: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"database unavailable: {type(exc).__name__}",
    )


def _duplicate_agent() -> DuplicateDetectionAgent:
    """The shared pipeline's duplicate agent, so inference state is not duplicated."""
    return get_pipeline().duplicate


def _snapshot_of(complaint: Complaint) -> ComplaintSnapshot:
    return ComplaintSnapshot(
        text=complaint.normalized_description or complaint.description,
        reference_id=complaint.reference_id,
        category=complaint.category,
        ward=complaint.ward,
        latitude=complaint.latitude,
        longitude=complaint.longitude,
    )


def _summary(
    complaint: Complaint,
    *,
    department: Department | None = None,
    duplicate_reference: str | None = None,
    evidence_count: int = 0,
    department_is_approved: bool = False,
) -> OfficerComplaintSummary:
    return OfficerComplaintSummary(
        complaint_id=complaint.id,
        reference_id=complaint.reference_id,
        status=complaint.status,
        pending_human_review=complaint.status not in DECIDED_STATUSES,
        category=complaint.category,
        priority=complaint.priority,
        language=complaint.language,
        ward=complaint.ward,
        location_text=complaint.location_text,
        recommended_department_code=department.code if department else None,
        recommended_department_name=department.name if department else None,
        department_is_approved=department_is_approved,
        classification_confidence=complaint.classification_confidence,
        urgency_score=complaint.urgency_score,
        reported_at=complaint.reported_at,
        submitted_at=complaint.submitted_at,
        sla_due_at=complaint.sla_due_at,
        sla_breached=complaint.sla_breached,
        is_duplicate=complaint.duplicate_of_id is not None,
        duplicate_of_reference=duplicate_reference,
        duplicate_similarity=complaint.duplicate_similarity,
        needs_clarification=complaint.needs_clarification,
        evidence_count=evidence_count,
    )


def _status_event(row: ComplaintStatusHistory) -> OfficerStatusEvent:
    return OfficerStatusEvent(
        history_id=row.id,
        from_status=row.from_status,
        to_status=row.to_status,
        changed_by_user_id=row.changed_by_user_id,
        changed_by_agent=row.changed_by_agent,
        reason=row.reason,
        notes=row.notes,
        changed_at=row.changed_at,
    )


def _agent_step(row: AgentExecutionLog) -> OfficerAgentStep:
    return OfficerAgentStep(
        agent_name=row.agent_name,
        step_index=row.step_index,
        status=row.status,
        explanation=row.explanation,
        confidence=row.confidence,
        model_name=row.model_name,
        duration_ms=row.duration_ms,
        workflow_run_id=row.workflow_run_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _parse(entry: AgentExecutionLog | None, model: type[BaseModel]):
    """Rebuild a stored agent result, or ``None`` when it is absent or malformed."""
    if entry is None or entry.output_payload is None:
        return None
    try:
        return model.model_validate(entry.output_payload)
    except ValueError:
        return None


def _stored_results(rows: list[AgentExecutionLog]) -> dict[AgentName, AgentExecutionLog]:
    """Index the last stored result per agent; evidence steps are read separately."""
    return {row.agent_name: row for row in rows if row.output_payload}


def _stored_evidence_results(rows: list[AgentExecutionLog]) -> list[EvidenceAnalysisResult]:
    """Every evidence step, in order: one image produces one analysis record."""
    results = []
    for row in rows:
        if row.agent_name is not AgentName.EVIDENCE_VERIFICATION:
            continue
        parsed = _parse(row, EvidenceAnalysisResult)
        if parsed is not None:
            results.append(parsed)
    return results


def _duplicate_reference_for(session, complaint: Complaint) -> str | None:
    if complaint.duplicate_of_id is None:
        return None
    return session.execute(
        select(Complaint.reference_id).where(Complaint.id == complaint.duplicate_of_id)
    ).scalar_one_or_none()


def _department_of(session, department_id: uuid.UUID | None) -> Department | None:
    if department_id is None:
        return None
    return session.get(Department, department_id)


def _approved_assignment(session, complaint_id: uuid.UUID) -> ComplaintAssignment | None:
    return session.execute(
        select(ComplaintAssignment)
        .where(
            ComplaintAssignment.complaint_id == complaint_id,
            ComplaintAssignment.approval_status == AssignmentApprovalStatus.APPROVED,
        )
        .order_by(ComplaintAssignment.approved_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _assignment_item(
    session, row: ComplaintAssignment, department: Department | None
) -> OfficerAssignmentItem:
    recommended = _department_of(session, row.ai_recommended_department_id)
    return OfficerAssignmentItem(
        assignment_id=row.id,
        department_code=department.code if department else None,
        department_name=department.name if department else None,
        officer_id=row.officer_id,
        ai_recommended_department_code=recommended.code if recommended else None,
        ai_confidence=row.ai_confidence,
        ai_explanation=row.ai_explanation,
        approval_status=row.approval_status,
        assigned_by_user_id=row.assigned_by_user_id,
        approved_by_user_id=row.approved_by_user_id,
        approved_at=row.approved_at,
        rejection_reason=row.rejection_reason,
        notes=row.notes,
        created_at=row.created_at,
    )


def _citizen_account(session, complaint: Complaint) -> OfficerCitizenAccount:
    if complaint.citizen_id is None:
        return OfficerCitizenAccount(is_synthetic=complaint.is_synthetic)
    user = session.get(User, complaint.citizen_id)
    if user is None:
        return OfficerCitizenAccount(is_synthetic=complaint.is_synthetic)
    return OfficerCitizenAccount(
        user_id=user.id,
        display_name=user.full_name,
        preferred_language=user.preferred_language,
        is_synthetic=complaint.is_synthetic,
    )


def _humanize(value: str) -> str:
    """Turn a stored enum value into a short display label."""
    return value.replace("_", " ").replace("-", " ").title()


def _buckets(counts: dict) -> list[AnalyticsBucket]:
    """Convert a ``{column_value: count}`` mapping into count-ordered buckets.

    A ``None`` key - an unclassified category or an unassigned priority - is
    surfaced explicitly rather than dropped, so the dashboard can show the
    complaints that still need a human's judgement.
    """
    buckets = []
    for key, count in counts.items():
        if key is None:
            buckets.append(AnalyticsBucket(key="unspecified", label="Unspecified", count=count))
        else:
            value = key.value if hasattr(key, "value") else str(key)
            buckets.append(AnalyticsBucket(key=value, label=_humanize(value), count=count))
    buckets.sort(key=lambda bucket: bucket.count, reverse=True)
    return buckets


def _trend(rows) -> list[AnalyticsTrendPoint]:
    """Convert ``(day, count)`` rows into ISO-dated trend points, oldest first."""
    return [
        AnalyticsTrendPoint(period=period.date().isoformat(), count=count)
        for period, count in rows
        if period is not None
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/complaints",
    response_model=OfficerComplaintPage,
    summary="List complaints for the officer dashboard",
    description=(
        "Paginated, filterable list of complaints, newest first. Filters cover "
        "status, priority, category, department, ward, language, the assigned "
        "department, and free-text search over the reference id and location. "
        "Read-only: no filter changes a complaint."
    ),
)
def list_complaints(
    session: SessionDep,
    officer: OfficerUser,
    complaint_status: Annotated[list[ComplaintStatus] | None, Query(alias="status")] = None,
    priority: Annotated[ComplaintPriority | None, Query()] = None,
    category: Annotated[ComplaintCategory | None, Query()] = None,
    department_id: Annotated[uuid.UUID | None, Query()] = None,
    ward: Annotated[str | None, Query(max_length=80)] = None,
    language: Annotated[ComplaintLanguage | None, Query()] = None,
    pending_review_only: Annotated[
        bool,
        Query(description="Restrict to complaints still awaiting an officer decision."),
    ] = False,
    search: Annotated[
        str | None,
        Query(max_length=120, description="Match against the reference id or location."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OfficerComplaintPage:
    statement = select(Complaint, Department).outerjoin(
        Department, Complaint.current_department_id == Department.id
    )
    count_statement = select(func.count()).select_from(Complaint)

    conditions = []
    if complaint_status:
        conditions.append(Complaint.status.in_(complaint_status))
    if pending_review_only:
        conditions.append(Complaint.status.notin_(DECIDED_STATUSES))
    if priority is not None:
        conditions.append(Complaint.priority == priority)
    if category is not None:
        conditions.append(Complaint.category == category)
    if department_id is not None:
        conditions.append(Complaint.current_department_id == department_id)
    if ward is not None:
        conditions.append(Complaint.ward == ward)
    if language is not None:
        conditions.append(Complaint.language == language)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            Complaint.reference_id.ilike(pattern)
            | func.coalesce(Complaint.location_text, "").ilike(pattern)
        )
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)

    try:
        total = session.execute(count_statement).scalar_one()
        rows = session.execute(
            statement.order_by(Complaint.reported_at.desc()).offset(offset).limit(limit)
        ).all()
        identifiers = [complaint.id for complaint, _ in rows]
        evidence_counts: dict[uuid.UUID, int] = {}
        if identifiers:
            evidence_counts = dict(
                session.execute(
                    select(ComplaintEvidence.complaint_id, func.count())
                    .where(ComplaintEvidence.complaint_id.in_(identifiers))
                    .group_by(ComplaintEvidence.complaint_id)
                ).all()
            )
        items = [
            _summary(
                complaint,
                department=department,
                evidence_count=evidence_counts.get(complaint.id, 0),
            )
            for complaint, department in rows
        ]
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return OfficerComplaintPage(
        total=total,
        limit=limit,
        offset=offset,
        returned=len(items),
        items=items,
    )


@router.get(
    "/review-queue",
    response_model=OfficerComplaintPage,
    summary="Complaints awaiting a human decision",
    description=(
        "The pending human-review queue: every complaint whose status is not yet "
        "a decided state. Read-only - it lists what a human still has to look at "
        "and changes nothing."
    ),
)
def review_queue(
    session: SessionDep,
    officer: OfficerUser,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OfficerComplaintPage:
    try:
        total = session.execute(
            select(func.count())
            .select_from(Complaint)
            .where(Complaint.status.notin_(DECIDED_STATUSES))
        ).scalar_one()
        rows = session.execute(
            select(Complaint, Department)
            .outerjoin(Department, Complaint.current_department_id == Department.id)
            .where(Complaint.status.notin_(DECIDED_STATUSES))
            .order_by(Complaint.reported_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        identifiers = [complaint.id for complaint, _ in rows]
        evidence_counts: dict[uuid.UUID, int] = {}
        if identifiers:
            evidence_counts = dict(
                session.execute(
                    select(ComplaintEvidence.complaint_id, func.count())
                    .where(ComplaintEvidence.complaint_id.in_(identifiers))
                    .group_by(ComplaintEvidence.complaint_id)
                ).all()
            )
        items = [
            _summary(
                complaint,
                department=department,
                evidence_count=evidence_counts.get(complaint.id, 0),
            )
            for complaint, department in rows
        ]
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return OfficerComplaintPage(
        total=total, limit=limit, offset=offset, returned=len(items), items=items
    )


@router.get(
    "/departments",
    response_model=DepartmentDirectoryPage,
    summary="The prototype department directory",
    description=(
        "The controlled directory the routing agent reads from. Returned so the "
        "dashboard can populate its filters and label a recommended department. "
        "Every row is prototype data, flagged as such."
    ),
)
def list_departments(session: SessionDep, officer: OfficerUser) -> DepartmentDirectoryPage:
    try:
        rows = session.execute(select(Department).order_by(Department.code)).scalars().all()
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return DepartmentDirectoryPage(
        total=len(rows),
        items=[
            DepartmentDirectoryItem(
                department_id=row.id,
                code=row.code,
                name=row.name,
                category=row.category,
                jurisdiction=row.jurisdiction,
                is_active=row.is_active,
                is_prototype_data=row.is_prototype_data,
            )
            for row in rows
        ],
    )


@router.get(
    "/analytics",
    response_model=OfficerAnalytics,
    summary="Aggregate workload and outcome figures",
    description=(
        "Server-side aggregation over the whole complaint table: totals, the "
        "pending-review count, the status, category, priority and language "
        "breakdowns, per-department workload and a daily submitted/resolved "
        "trend. Read-only, and the figures describe the stored prototype data "
        "rather than any verified government statistic."
    ),
)
def get_analytics(
    session: SessionDep,
    officer: OfficerUser,
    window_days: Annotated[int, Query(ge=7, le=365, description="Trend window in days.")] = 30,
) -> OfficerAnalytics:
    since = datetime.now(UTC) - timedelta(days=window_days)
    try:

        def count_where(*conditions) -> int:
            statement = select(func.count()).select_from(Complaint)
            if conditions:
                statement = statement.where(*conditions)
            return session.execute(statement).scalar_one()

        def breakdown(column) -> dict:
            return dict(session.execute(select(column, func.count()).group_by(column)).all())

        total = count_where()
        pending = count_where(Complaint.status.notin_(DECIDED_STATUSES))
        sla_breached = count_where(Complaint.sla_breached.is_(True))
        awaiting_triage = count_where(Complaint.category.is_(None))
        unassigned = count_where(Complaint.current_department_id.is_(None))

        by_status = breakdown(Complaint.status)
        by_category = breakdown(Complaint.category)
        by_priority = breakdown(Complaint.priority)
        by_language = breakdown(Complaint.language)

        department_rows = session.execute(
            select(Department.code, Department.name, func.count(Complaint.id))
            .join(Complaint, Complaint.current_department_id == Department.id)
            .group_by(Department.code, Department.name)
            .order_by(func.count(Complaint.id).desc())
        ).all()

        submitted_expr = func.date_trunc("day", Complaint.reported_at)
        submitted_rows = session.execute(
            select(submitted_expr, func.count())
            .where(Complaint.reported_at >= since)
            .group_by(submitted_expr)
            .order_by(submitted_expr)
        ).all()

        resolved_expr = func.date_trunc("day", ComplaintStatusHistory.changed_at)
        resolved_rows = session.execute(
            select(resolved_expr, func.count())
            .where(
                ComplaintStatusHistory.to_status.in_(
                    [ComplaintStatus.RESOLVED, ComplaintStatus.CLOSED]
                ),
                ComplaintStatusHistory.changed_at >= since,
            )
            .group_by(resolved_expr)
            .order_by(resolved_expr)
        ).all()
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return OfficerAnalytics(
        generated_at=datetime.now(UTC),
        window_days=window_days,
        total_complaints=total,
        pending_review=pending,
        decided=total - pending,
        sla_breached=sla_breached,
        awaiting_triage=awaiting_triage,
        unassigned=unassigned,
        by_status=_buckets(by_status),
        by_category=_buckets(by_category),
        by_priority=_buckets(by_priority),
        by_language=_buckets(by_language),
        by_department=[
            AnalyticsBucket(key=code, label=name, count=count)
            for code, name, count in department_rows
        ],
        submitted_trend=_trend(submitted_rows),
        resolved_trend=_trend(resolved_rows),
    )


@router.get(
    "/complaints/{complaint_id}",
    response_model=OfficerComplaintDetail,
    summary="Full detail view of one complaint",
    description=(
        "Bundles the citizen's verbatim text, the stored intake and classification "
        "results, a fresh routing and resolution recommendation, the ranked "
        "potential duplicate matches with their similarity reasons, the evidence "
        "analysis, the assignment history and the audit trail. Read-only - the "
        "approval and rejection routes are named in ``decision_endpoints`` but "
        "are never called from here."
    ),
)
def get_complaint_detail(
    complaint_id: uuid.UUID, session: SessionDep, officer: OfficerUser
) -> OfficerComplaintDetail:
    try:
        complaint = session.get(Complaint, complaint_id)
        if complaint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"complaint {complaint_id} not found"
            )

        department = _department_of(session, complaint.current_department_id)
        approved = _approved_assignment(session, complaint.id)

        logs = (
            session.execute(
                select(AgentExecutionLog)
                .where(AgentExecutionLog.complaint_id == complaint.id)
                .order_by(AgentExecutionLog.step_index)
            )
            .scalars()
            .all()
        )
        evidence_rows = (
            session.execute(
                select(ComplaintEvidence)
                .where(ComplaintEvidence.complaint_id == complaint.id)
                .order_by(ComplaintEvidence.uploaded_at)
            )
            .scalars()
            .all()
        )
        assignment_rows = (
            session.execute(
                select(ComplaintAssignment)
                .where(ComplaintAssignment.complaint_id == complaint.id)
                .order_by(ComplaintAssignment.created_at)
            )
            .scalars()
            .all()
        )
        history_rows = (
            session.execute(
                select(ComplaintStatusHistory)
                .where(ComplaintStatusHistory.complaint_id == complaint.id)
                .order_by(ComplaintStatusHistory.changed_at)
            )
            .scalars()
            .all()
        )

        candidates = (
            session.execute(
                select(Complaint)
                .where(Complaint.id != complaint.id)
                .order_by(Complaint.reported_at.desc())
                .limit(CANDIDATE_SCAN_LIMIT)
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    stored = _stored_results(logs)
    duplicate_result = _duplicate_agent().run(
        _snapshot_of(complaint), [_snapshot_of(row) for row in candidates]
    )
    routing_result = _ROUTING_AGENT.run(
        RoutingInput(
            text=complaint.normalized_description or complaint.description,
            category=complaint.category,
            ward=complaint.ward,
            location_text=complaint.location_text,
            latitude=complaint.latitude,
            longitude=complaint.longitude,
        )
    )
    resolution_result = _RESOLUTION_AGENT.run(
        ResolutionInput(
            text=complaint.normalized_description or complaint.description,
            category=complaint.category,
            ward=complaint.ward,
            available_evidence=tuple(row.file_name for row in evidence_rows),
        )
    )

    analyses_by_file = {}
    for analysis in _stored_evidence_results(logs):
        if analysis.file_name:
            analyses_by_file[analysis.file_name] = analysis
    evidence_items = []
    for row in evidence_rows:
        evidence_items.append(
            OfficerEvidenceItem(
                evidence_id=row.id,
                file_name=row.file_name,
                mime_type=row.mime_type,
                file_size_bytes=row.file_size_bytes,
                caption=row.caption,
                verification_status=row.verification_status,
                relevance_score=row.relevance_score,
                requires_manual_review=row.requires_manual_review,
                verification_notes=row.verification_notes,
                agent_explanation=row.agent_explanation,
                analysis_model=row.analysis_model,
                uploaded_at=row.uploaded_at,
                analysis=analyses_by_file.get(row.file_name),
            )
        )

    assignment_items = []
    for row in assignment_rows:
        assignment_items.append(
            _assignment_item(session, row, _department_of(session, row.department_id))
        )

    return OfficerComplaintDetail(
        complaint=_summary(
            complaint,
            department=department,
            duplicate_reference=_duplicate_reference_for(session, complaint),
            evidence_count=len(evidence_rows),
            department_is_approved=approved is not None,
        ),
        citizen_account=_citizen_account(session, complaint),
        original_text=complaint.description,
        normalized_text=complaint.normalized_description,
        intake=_parse(stored.get(AgentName.INTAKE), IntakeResult),
        classification=_parse(stored.get(AgentName.CLASSIFICATION), ClassificationResult),
        recommended_department=routing_result,
        resolution=resolution_result,
        duplicate_matches=duplicate_result,
        evidence=evidence_items,
        assignments=assignment_items,
        status_history=[_status_event(row) for row in history_rows],
        agent_steps=[_agent_step(row) for row in logs],
        decision_endpoints={
            "duplicate_review": f"/api/complaints/{complaint.id}/duplicates/review",
            "routing_decision": f"/api/complaints/{complaint.id}/routing/decision",
            "routing_recommendation": f"/api/complaints/{complaint.id}/routing",
            "duplicate_matches": f"/api/complaints/{complaint.id}/duplicates",
        },
    )


@router.get(
    "/complaints/{complaint_id}/audit",
    response_model=OfficerAuditTrail,
    summary="Audit history and status information for one complaint",
    description=(
        "The append-only status transitions, every routing decision and the "
        "recorded agent steps. Read-only."
    ),
)
def get_complaint_audit(
    complaint_id: uuid.UUID, session: SessionDep, officer: OfficerUser
) -> OfficerAuditTrail:
    try:
        complaint = session.get(Complaint, complaint_id)
        if complaint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"complaint {complaint_id} not found"
            )
        history_rows = (
            session.execute(
                select(ComplaintStatusHistory)
                .where(ComplaintStatusHistory.complaint_id == complaint.id)
                .order_by(ComplaintStatusHistory.changed_at)
            )
            .scalars()
            .all()
        )
        assignment_rows = (
            session.execute(
                select(ComplaintAssignment)
                .where(ComplaintAssignment.complaint_id == complaint.id)
                .order_by(ComplaintAssignment.created_at)
            )
            .scalars()
            .all()
        )
        log_rows = (
            session.execute(
                select(AgentExecutionLog)
                .where(AgentExecutionLog.complaint_id == complaint.id)
                .order_by(AgentExecutionLog.step_index)
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return OfficerAuditTrail(
        complaint_id=complaint.id,
        reference_id=complaint.reference_id,
        current_status=complaint.status,
        status_history=[_status_event(row) for row in history_rows],
        assignments=[
            _assignment_item(session, row, _department_of(session, row.department_id))
            for row in assignment_rows
        ],
        agent_steps=[_agent_step(row) for row in log_rows],
    )


__all__ = [
    "DECIDED_STATUSES",
    "SUGGESTION_NOTICE",
    "DepartmentDirectoryPage",
    "OfficerAnalytics",
    "OfficerAuditTrail",
    "OfficerComplaintDetail",
    "OfficerComplaintPage",
    "OfficerComplaintSummary",
    "router",
]
