"""Phase 7 citizen portal endpoints.

    POST /api/citizen/complaints                 -> ComplaintSubmissionResponse
    GET  /api/citizen/complaints                 -> CitizenComplaintPage
    GET  /api/citizen/complaints/{reference_id}  -> CitizenComplaintDetail

A signed-in citizen submits a complaint in English or Tamil, optionally picks a
category and a location and attaches evidence images. The endpoint reuses the
Phase 6 versioned workflow for the AI analysis and the Phase 3 persistence layer
for storage; the citizen's own text is written verbatim.

Two boundaries are structural:

* **The citizen's submission is preserved.** ``complaints.description`` holds the
  citizen's words exactly as typed. A citizen-supplied category, location and
  language are stored as *citizen facts* and only ever used where the agent
  could not determine a value.
* **The AI output is a suggestion.** Every classification, priority, department,
  duplicate match and resolution step is returned under a clearly-labelled
  ``analysis`` block with an explicit notice: nothing is approved, assigned,
  merged, resolved or closed here, and an officer decides all of it.

A citizen can only ever read back their own complaints: ``/complaints/{ref}``
answers ``404`` for a reference that belongs to somebody else, so the endpoint
cannot be used to confirm that another citizen's complaint exists.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agents.evidence import EvidenceImage
from app.agents.persistence import generate_reference_id, persist_analysis
from app.agents.workflow import WorkflowInputs, get_workflow
from app.api.deps import CitizenUser, SessionDep
from app.core.logging import get_logger
from app.core.uploads import UploadValidationError, ValidatedUpload, validate_image_upload
from app.models.agent_execution_log import AgentExecutionLog
from app.models.complaint import Complaint
from app.models.complaint_evidence import ComplaintEvidence
from app.models.enums import (
    AgentName,
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    ComplaintStatus,
)
from app.models.status_history import ComplaintStatusHistory
from app.schemas.agent import (
    ANALYSIS_PROVENANCE,
    ClassificationResult,
    ComplaintAnalysis,
    IntakeResult,
    ResolutionRecommendation,
    RoutingRecommendation,
)
from app.schemas.workflow import WorkflowOutcome, WorkflowRunResult

logger = get_logger(__name__)

router = APIRouter(prefix="/api/citizen", tags=["citizen"])

#: How many stored complaints are read as duplicate-detection candidates when a
#: citizen submits. Bounded so one submission cannot scan the whole table.
CANDIDATE_SCAN_LIMIT = 100

#: Repeated wherever the AI block is surfaced, so no reader mistakes a
#: suggestion for a decision that has already been taken.
SUGGESTION_NOTICE = (
    "The classification, priority, suggested department, duplicate matches and "
    "resolution steps below are AI-generated suggestions for a human officer to "
    "review. Your complaint has been recorded exactly as you wrote it; nothing "
    "has been approved, assigned, merged, resolved or closed."
)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CitizenEvidenceInput(BaseModel):
    """One optional evidence image, supplied as base64 bytes."""

    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, max_length=255, description="Original file name.")
    content_base64: str = Field(min_length=1, description="The image bytes, base64-encoded.")
    content_type: str | None = Field(
        default=None, description="The MIME type the uploader believes the file is."
    )

    def validate(self) -> ValidatedUpload:
        """Decode and validate the image: size, declared type, magic bytes and content."""
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"content_base64 for {self.file_name!r} is not valid base64 data",
            ) from exc
        try:
            return validate_image_upload(self.file_name, content, declared_type=self.content_type)
        except UploadValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"the uploaded file {self.file_name!r} was rejected: {exc}",
            ) from exc

    def to_image(self) -> EvidenceImage:
        validated = self.validate()
        return EvidenceImage.from_bytes(validated.file_name, validated.content)


class ComplaintSubmissionRequest(BaseModel):
    """A citizen's complaint, with optional category, location and evidence."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=5000,
        description="The complaint in English or Tamil, kept verbatim.",
    )
    language: ComplaintLanguage | None = Field(
        default=None,
        description="Optional; the citizen's declared language. Detected when omitted.",
    )
    category: ComplaintCategory | None = Field(
        default=None,
        description="Optional; the citizen's own category selection, if they made one.",
    )
    location_text: str | None = Field(default=None, max_length=255)
    ward: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    evidence: list[CitizenEvidenceInput] = Field(default_factory=list)

    request_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional idempotency key; a repeat with the same key is not re-recorded.",
    )

    @model_validator(mode="after")
    def _validate(self) -> ComplaintSubmissionRequest:
        if not self.text.strip():
            raise ValueError("the complaint text must not be blank")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self

    def to_inputs(self, candidates: list) -> tuple[WorkflowInputs, list[ValidatedUpload]]:
        uploads = [item.validate() for item in self.evidence]
        inputs = WorkflowInputs(
            candidates=candidates,
            images=[
                EvidenceImage.from_bytes(upload.file_name, upload.content) for upload in uploads
            ],
            interrupt_for_review=False,
            persist=False,
        )
        return inputs, uploads


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CitizenComplaintSummary(BaseModel):
    """A citizen-facing view of one of their own complaints."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: uuid.UUID
    reference_id: str
    status: ComplaintStatus
    category: ComplaintCategory | None = None
    priority: ComplaintPriority | None = None
    language: ComplaintLanguage
    location_text: str | None = None
    ward: str | None = None
    submitted_at: datetime | None = None
    is_duplicate: bool = False
    duplicate_of_reference: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None


class ComplaintSubmissionResponse(BaseModel):
    """The citizen's recorded complaint plus the AI's clearly-labelled suggestions."""

    model_config = ConfigDict(extra="forbid")

    complaint: CitizenComplaintSummary
    original_text: str = Field(description="The citizen's text, preserved verbatim.")

    analysis: WorkflowRunResult | None = Field(
        default=None,
        description="The AI suggestion block, or null when it could not be produced.",
    )
    analysis_outcome: WorkflowOutcome | str
    replayed: bool = Field(
        default=False, description="True when an identical request_id was already recorded."
    )

    suggestion_notice: str = SUGGESTION_NOTICE
    provenance: str = ANALYSIS_PROVENANCE
    message: str


class CitizenComplaintPage(BaseModel):
    """A page of a citizen's own complaints, newest first."""

    model_config = ConfigDict(extra="forbid")

    total: int
    limit: int
    offset: int
    returned: int
    items: list[CitizenComplaintSummary] = Field(default_factory=list)


class CitizenComplaintDetail(BaseModel):
    """One of the citizen's own complaints with its visible audit trail."""

    model_config = ConfigDict(extra="forbid")

    complaint: CitizenComplaintSummary
    original_text: str
    status_history: list[dict] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    suggestion_notice: str = SUGGESTION_NOTICE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _database_unavailable(exc: SQLAlchemyError) -> HTTPException:
    logger.warning("Citizen endpoint database error: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"database unavailable: {type(exc).__name__}",
    )


def _summary(row: Complaint, *, duplicate_reference: str | None = None) -> CitizenComplaintSummary:
    return CitizenComplaintSummary(
        complaint_id=row.id,
        reference_id=row.reference_id,
        status=row.status,
        category=row.category,
        priority=row.priority,
        language=row.language,
        location_text=row.location_text,
        ward=row.ward,
        submitted_at=row.submitted_at,
        is_duplicate=row.duplicate_of_id is not None,
        duplicate_of_reference=duplicate_reference,
        needs_clarification=row.needs_clarification,
        clarification_question=row.clarification_question,
    )


def _load_candidates(session) -> list:
    from app.agents.duplicate import ComplaintSnapshot

    rows = (
        session.execute(
            select(Complaint).order_by(Complaint.reported_at.desc()).limit(CANDIDATE_SCAN_LIMIT)
        )
        .scalars()
        .all()
    )
    return [
        ComplaintSnapshot(
            text=row.normalized_description or row.description,
            reference_id=row.reference_id,
            category=row.category,
            ward=row.ward,
            latitude=row.latitude,
            longitude=row.longitude,
        )
        for row in rows
    ]


def _existing_complaint_id(session, workflow_run_id: str) -> uuid.UUID | None:
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


def _analysis_from_logs(session, complaint: Complaint) -> WorkflowRunResult | None:
    """Rebuild the suggestion block from the stored agent logs, without re-running."""
    logs = (
        session.execute(
            select(AgentExecutionLog)
            .where(AgentExecutionLog.complaint_id == complaint.id)
            .order_by(AgentExecutionLog.step_index)
        )
        .scalars()
        .all()
    )
    by_agent = {log.agent_name: log for log in logs if log.output_payload}

    def parse(agent: AgentName, model: type[BaseModel]):
        entry = by_agent.get(agent)
        if entry is None or entry.output_payload is None:
            return None
        try:
            return model.model_validate(entry.output_payload)
        except ValueError:
            return None

    intake = parse(AgentName.INTAKE, IntakeResult)
    if intake is None:
        return None
    classification = parse(AgentName.CLASSIFICATION, ClassificationResult)
    routing = parse(AgentName.DEPARTMENT_ROUTING, RoutingRecommendation)
    resolution = parse(AgentName.RESOLUTION_RECOMMENDATION, ResolutionRecommendation)

    run_id = next((log.workflow_run_id for log in logs if log.workflow_run_id), None)
    return WorkflowRunResult(
        workflow_run_id=run_id or f"replay-{complaint.id}",
        outcome=WorkflowOutcome.COMPLETED,
        complaint_id=str(complaint.id),
        reference_id=complaint.reference_id,
        intake=intake,
        classification=classification,
        routing=routing,
        resolution=resolution,
        persisted=True,
        total_latency_ms=0.0,
    )


def _persist_minimal(
    session, payload: ComplaintSubmissionRequest, citizen_id: uuid.UUID
) -> Complaint:
    """Record the citizen's complaint even when the agent pipeline was unusable.

    The complaint text is stored verbatim and the row is left unclassified; the
    officer dashboard shows it as needing manual triage.
    """
    complaint = Complaint(
        reference_id=generate_reference_id(session),
        citizen_id=citizen_id,
        description=payload.text,
        language=payload.language or ComplaintLanguage.EN,
        location_text=payload.location_text,
        ward=payload.ward,
        latitude=payload.latitude,
        longitude=payload.longitude,
        status=ComplaintStatus.SUBMITTED,
        category=payload.category,
        is_synthetic=False,
    )
    session.add(complaint)
    session.flush()
    return complaint


def _record_initial_history(session, complaint: Complaint) -> None:
    session.add(
        ComplaintStatusHistory(
            complaint_id=complaint.id,
            from_status=None,
            to_status=ComplaintStatus.SUBMITTED,
            changed_by_agent="citizen_portal",
            reason="Complaint submitted through the citizen portal",
        )
    )


def _record_evidence(
    session, complaint: Complaint, analyses, uploads: list[ValidatedUpload]
) -> None:
    """Store one evidence row per analysed image, with its validated metadata.

    The validated upload supplies the file name, MIME type and byte size; the
    agent supplies the analysis. Both are recorded so an officer sees the real
    file facts alongside the AI's opinion of it.
    """
    by_name = {upload.file_name: upload for upload in uploads}
    for analysis in analyses:
        name = analysis.file_name or "evidence"
        upload = by_name.get(name)
        session.add(
            ComplaintEvidence(
                complaint_id=complaint.id,
                file_name=name,
                storage_path=name,
                mime_type=upload.mime_type if upload else None,
                file_size_bytes=upload.size_bytes if upload else None,
                caption=None,
                verification_status=analysis.verification_status,
                relevance_score=analysis.relevance_score,
                requires_manual_review=analysis.requires_manual_review,
                verification_notes=analysis.verification_notes,
                agent_explanation=analysis.reasoning,
                analysis_model=analysis.model_name,
            )
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/complaints",
    response_model=ComplaintSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a complaint (English or Tamil)",
    description=(
        "Records the citizen's complaint verbatim, then runs the versioned "
        "orchestration workflow to produce AI *suggestions*: a category, a "
        "priority, a suggested department, potential duplicates and resolution "
        "steps. The suggestions are advisory only and are labelled as such. "
        "Requires a signed-in citizen. Repeating the same request_id does not "
        "create a second complaint."
    ),
)
def submit_complaint(
    payload: ComplaintSubmissionRequest, session: SessionDep, citizen: CitizenUser
) -> ComplaintSubmissionResponse:
    run_id = payload.request_id

    try:
        if run_id:
            existing_id = _existing_complaint_id(session, run_id)
            if existing_id is not None:
                existing = session.get(Complaint, existing_id)
                if existing is not None and existing.citizen_id == citizen.id:
                    return ComplaintSubmissionResponse(
                        complaint=_summary(existing),
                        original_text=existing.description,
                        analysis=_analysis_from_logs(session, existing),
                        analysis_outcome="replayed",
                        replayed=True,
                        message=(
                            "this request was already recorded; returning the existing "
                            f"complaint {existing.reference_id}"
                        ),
                    )

        candidates = _load_candidates(session)
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    workflow = get_workflow()
    inputs, uploads = payload.to_inputs(candidates)
    result = workflow.invoke(
        text=payload.text,
        inputs=inputs,
        request_id=run_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )

    try:
        if result.intake is not None and result.classification is not None:
            analysis = ComplaintAnalysis(
                source_text=payload.text,
                intake=result.intake,
                classification=result.classification,
                duplicate=result.duplicate,
                evidence=result.evidence,
                llm_calls=sum(
                    int(bool(item.llm_used))
                    for item in (result.intake, result.classification, result.duplicate)
                    if item is not None
                )
                + sum(int(bool(item.llm_used)) for item in result.evidence),
                total_latency_ms=result.total_latency_ms,
                steps=[entry.node for entry in result.nodes],
                generated_at=datetime.now(UTC),
            )
            complaint = persist_analysis(
                session,
                analysis,
                citizen_id=citizen.id,
                is_synthetic=False,
                workflow_run_id=result.workflow_run_id,
            )
            # Citizen-supplied facts win where the agent could not determine a value.
            if payload.category is not None and complaint.category is None:
                complaint.category = payload.category
            if payload.location_text and not complaint.location_text:
                complaint.location_text = payload.location_text
            if payload.ward and not complaint.ward:
                complaint.ward = payload.ward
            if payload.latitude is not None and complaint.latitude is None:
                complaint.latitude = payload.latitude
                complaint.longitude = payload.longitude
            if payload.language is not None:
                complaint.language = payload.language
            _record_evidence(session, complaint, result.evidence, uploads)
        else:
            complaint = _persist_minimal(session, payload, citizen.id)
            if payload.evidence:
                logger.info("Evidence analysis skipped; the agent pipeline was unusable")

        _record_initial_history(session, complaint)
        session.commit()
        session.refresh(complaint)
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    logger.info(
        "Citizen %s recorded complaint %s (outcome=%s)",
        citizen.id,
        complaint.reference_id,
        result.outcome.value,
    )
    return ComplaintSubmissionResponse(
        complaint=_summary(complaint),
        original_text=complaint.description,
        analysis=result,
        analysis_outcome=result.outcome,
        message=(
            f"complaint {complaint.reference_id} recorded with status "
            f"'{complaint.status.value}'. The AI suggestions are advisory only."
        ),
    )


@router.get(
    "/complaints",
    response_model=CitizenComplaintPage,
    summary="List the caller's own complaints",
    description="Returns only the complaints submitted by the signed-in citizen.",
)
def list_my_complaints(
    session: SessionDep,
    citizen: CitizenUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> CitizenComplaintPage:
    try:
        rows = (
            session.execute(
                select(Complaint)
                .where(Complaint.citizen_id == citizen.id)
                .order_by(Complaint.submitted_at.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return CitizenComplaintPage(
        total=len(rows),
        limit=limit,
        offset=offset,
        returned=len(rows),
        items=[_summary(row) for row in rows],
    )


@router.get(
    "/complaints/{reference_id}",
    response_model=CitizenComplaintDetail,
    summary="Look up one of the caller's complaints by reference",
    description=(
        "Returns the complaint, its status and its visible audit trail. A "
        "reference belonging to another citizen answers 404 so the endpoint "
        "cannot confirm that somebody else's complaint exists."
    ),
)
def get_my_complaint(
    reference_id: str, session: SessionDep, citizen: CitizenUser
) -> CitizenComplaintDetail:
    reference = reference_id.strip().upper()
    try:
        complaint = session.execute(
            select(Complaint).where(Complaint.reference_id == reference)
        ).scalar_one_or_none()
        if complaint is None or complaint.citizen_id != citizen.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"complaint {reference} not found",
            )
        history = (
            session.execute(
                select(ComplaintStatusHistory)
                .where(ComplaintStatusHistory.complaint_id == complaint.id)
                .order_by(ComplaintStatusHistory.changed_at)
            )
            .scalars()
            .all()
        )
        evidence = (
            session.execute(
                select(ComplaintEvidence)
                .where(ComplaintEvidence.complaint_id == complaint.id)
                .order_by(ComplaintEvidence.uploaded_at)
            )
            .scalars()
            .all()
        )
        duplicate_reference = None
        if complaint.duplicate_of_id is not None:
            duplicate_reference = session.execute(
                select(Complaint.reference_id).where(Complaint.id == complaint.duplicate_of_id)
            ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return CitizenComplaintDetail(
        complaint=_summary(complaint, duplicate_reference=duplicate_reference),
        original_text=complaint.description,
        status_history=[
            {
                "from_status": row.from_status.value if row.from_status else None,
                "to_status": row.to_status.value,
                "changed_at": row.changed_at.isoformat() if row.changed_at else None,
                "reason": row.reason,
                "by_agent": row.changed_by_agent,
            }
            for row in history
        ],
        evidence=[
            {
                "evidence_id": str(row.id),
                "file_name": row.file_name,
                "verification_status": row.verification_status.value,
                "requires_manual_review": row.requires_manual_review,
                "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
            }
            for row in evidence
        ],
    )


__all__ = [
    "CitizenComplaintDetail",
    "CitizenComplaintPage",
    "CitizenComplaintSummary",
    "CitizenEvidenceInput",
    "ComplaintSubmissionRequest",
    "ComplaintSubmissionResponse",
    "router",
]
