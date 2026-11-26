"""Phase 4 duplicate-detection endpoints and the officer link-review workflow.

    POST /api/duplicates/analyze                           -> DuplicateDetectionResult
    GET  /api/complaints/{complaint_id}/duplicates         -> DuplicateMatchesPage
    POST /api/complaints/{complaint_id}/duplicates/review  -> DuplicateReviewResult

The detection agent only ever **recommends**: it returns *potential* matches with
a similarity score and the reasons behind it, and never declares two complaints
identical. ``complaints.duplicate_of_id`` is therefore written by exactly one
code path in the whole application - the explicit officer review call below -
and every decision, approve or reject, appends a ``complaint_status_history``
row. Nothing here merges, links or rejects a complaint on its own.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.duplicate import ComplaintSnapshot, DuplicateDetectionAgent
from app.agents.pipeline import get_pipeline
from app.api.deps import OfficerUser, SessionDep
from app.core.logging import get_logger
from app.models.complaint import Complaint
from app.models.enums import ComplaintCategory, ComplaintStatus
from app.models.status_history import ComplaintStatusHistory
from app.schemas.agent import DuplicateCandidate, DuplicateDetectionResult

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["duplicates"])

DEFAULT_MATCH_LIMIT = 10
MAX_MATCH_LIMIT = 50
DEFAULT_CANDIDATE_SCAN = 100
MAX_CANDIDATE_SCAN = 500


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class DuplicateCandidateInput(BaseModel):
    """One complaint supplied inline, in place of a stored row."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The candidate complaint's wording.")
    reference_id: str | None = None
    category: ComplaintCategory | None = None
    ward: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)

    @model_validator(mode="after")
    def _coordinates_are_paired(self) -> DuplicateCandidateInput:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self

    def to_snapshot(self) -> ComplaintSnapshot:
        return ComplaintSnapshot(
            text=self.text,
            reference_id=self.reference_id,
            category=self.category,
            ward=self.ward,
            latitude=self.latitude,
            longitude=self.longitude,
        )


class DuplicateAnalyzeRequest(BaseModel):
    """A complaint to compare against stored complaints or inline candidates."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The complaint to look for duplicates of.")
    reference_id: str | None = None
    category: ComplaintCategory | None = None
    ward: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)

    candidates: list[DuplicateCandidateInput] = Field(
        default_factory=list,
        description="Comparison set supplied inline; when empty, stored complaints are scanned.",
    )
    complaint_id: uuid.UUID | None = Field(
        default=None,
        description="A stored complaint to exclude from the scan; validated when given.",
    )
    max_candidates: int = Field(
        default=DEFAULT_CANDIDATE_SCAN,
        ge=1,
        le=MAX_CANDIDATE_SCAN,
        description="Upper bound on stored complaints read for one request.",
    )

    @model_validator(mode="after")
    def _require_inputs(self) -> DuplicateAnalyzeRequest:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if not self.candidates and self.complaint_id is None:
            raise ValueError("provide at least one of 'candidates' or 'complaint_id'")
        return self

    def to_snapshot(self) -> ComplaintSnapshot:
        return ComplaintSnapshot(
            text=self.text,
            reference_id=self.reference_id,
            category=self.category,
            ward=self.ward,
            latitude=self.latitude,
            longitude=self.longitude,
        )


class DuplicateMatchesPage(BaseModel):
    """A page of ranked potential matches for one stored complaint."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: uuid.UUID
    reference_id: str
    subject_text: str

    candidates_considered: int = Field(description="How many stored complaints were compared.")
    total_matches: int = Field(description="Ranked matches before pagination.")
    limit: int
    offset: int
    returned: int
    items: list[DuplicateCandidate] = Field(default_factory=list)

    best_match: DuplicateCandidate | None = None
    likely_duplicate_found: bool = False
    threshold: float = Field(ge=0.0, le=1.0)
    text_only_threshold: float = Field(ge=0.0, le=1.0)
    geographic_radius_m: float = Field(ge=0.0)
    weights: dict[str, float] = Field(default_factory=dict)

    linking_requires_human_approval: bool = True
    reasoning: str


class DuplicateReviewRequest(BaseModel):
    """An officer's decision on one proposed duplicate link."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: uuid.UUID = Field(
        description="The stored complaint proposed as the duplicate of the path complaint."
    )
    decision: Literal["approve", "reject"] = Field(
        description="'approve' links the two complaints; 'reject' leaves them separate."
    )
    reviewer_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional; when given it must equal the authenticated officer's id. "
            "The server always records the session's account for the audit trail."
        ),
    )
    reason: str | None = None
    notes: str | None = None


class DuplicateReviewResult(BaseModel):
    """The recorded outcome of an officer's link decision."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: uuid.UUID
    reference_id: str
    candidate_id: uuid.UUID
    candidate_reference_id: str

    decision: Literal["approve", "reject"]
    linked: bool
    match_score: float = Field(ge=0.0, le=1.0)
    text_similarity: float = Field(ge=0.0, le=1.0)
    explanation: str

    status: ComplaintStatus
    audit_recorded: bool
    audit_record_id: uuid.UUID | None = None
    linking_requires_human_approval: bool = True
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _load_stored_candidates(
    session: Session, *, exclude_id: uuid.UUID | None, limit: int
) -> list[ComplaintSnapshot]:
    """Read up to ``limit`` stored complaints, newest first, for comparison."""
    statement = select(Complaint).order_by(Complaint.reported_at.desc()).limit(limit)
    if exclude_id is not None:
        statement = statement.where(Complaint.id != exclude_id)
    rows = session.execute(statement).scalars().all()
    return [_snapshot_of(row) for row in rows]


def _database_unavailable(exc: SQLAlchemyError) -> HTTPException:
    logger.warning("Duplicate endpoint database error: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"database unavailable: {type(exc).__name__}",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/duplicates/analyze",
    response_model=DuplicateDetectionResult,
    summary="Analyse a complaint for potential duplicates",
    description=(
        "Compares a complaint against a comparison set and returns ranked "
        "*potential* matches, each with its similarity score and the reasons "
        "behind it. The set is supplied inline via ``candidates`` or read from "
        "stored complaints (``complaint_id`` excludes the subject itself). "
        "The agent never merges, links or rejects anything - a human decides."
    ),
)
def analyze_for_duplicates(
    payload: DuplicateAnalyzeRequest, session: SessionDep, officer: OfficerUser
) -> DuplicateDetectionResult:
    if payload.candidates:
        candidates = [candidate.to_snapshot() for candidate in payload.candidates]
    else:
        try:
            if session.get(Complaint, payload.complaint_id) is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail=f"complaint {payload.complaint_id} not found",
                )
            candidates = _load_stored_candidates(
                session, exclude_id=payload.complaint_id, limit=payload.max_candidates
            )
        except SQLAlchemyError as exc:
            session.rollback()
            raise _database_unavailable(exc) from exc

    return _duplicate_agent().run(payload.to_snapshot(), candidates)


@router.get(
    "/complaints/{complaint_id}/duplicates",
    response_model=DuplicateMatchesPage,
    summary="Retrieve potential duplicate matches for a stored complaint",
    description=(
        "Runs duplicate detection for a stored complaint against the other "
        "stored complaints and returns the ranked matches, paginated. "
        "``total_matches`` is the full ranking; ``items`` is the requested page."
    ),
)
def list_duplicate_matches(
    complaint_id: uuid.UUID,
    session: SessionDep,
    officer: OfficerUser,
    limit: int = Query(DEFAULT_MATCH_LIMIT, ge=1, le=MAX_MATCH_LIMIT),
    offset: int = Query(0, ge=0),
    max_candidates: int = Query(DEFAULT_CANDIDATE_SCAN, ge=1, le=MAX_CANDIDATE_SCAN),
) -> DuplicateMatchesPage:
    try:
        complaint = session.get(Complaint, complaint_id)
        if complaint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"complaint {complaint_id} not found"
            )
        candidates = _load_stored_candidates(session, exclude_id=complaint_id, limit=max_candidates)
        result = _duplicate_agent().run(_snapshot_of(complaint), candidates)

        items = result.matches[offset : offset + limit]
        # The agent reports candidates by reference id only. The review endpoint
        # takes the candidate's row id, so the identifiers are resolved here -
        # on the officer-facing route only, so a citizen's submission response
        # never carries another citizen's internal id.
        if items:
            rows = session.execute(
                select(Complaint.reference_id, Complaint.id).where(
                    Complaint.reference_id.in_(item.reference_id for item in items)
                )
            ).all()
            identifiers = dict(rows)
            for item in items:
                item.complaint_id = identifiers.get(item.reference_id)
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return DuplicateMatchesPage(
        complaint_id=complaint.id,
        reference_id=complaint.reference_id,
        subject_text=result.subject_text,
        candidates_considered=result.candidates_considered,
        total_matches=len(result.matches),
        limit=limit,
        offset=offset,
        returned=len(items),
        items=items,
        best_match=result.best_match,
        likely_duplicate_found=result.likely_duplicate_found,
        threshold=result.threshold,
        text_only_threshold=result.text_only_threshold,
        geographic_radius_m=result.geographic_radius_m,
        weights=result.weights,
        linking_requires_human_approval=result.linking_requires_human_approval,
        reasoning=result.reasoning,
    )


@router.post(
    "/complaints/{complaint_id}/duplicates/review",
    response_model=DuplicateReviewResult,
    summary="Approve or reject a proposed duplicate link (human decision)",
    description=(
        "The only code path that writes ``duplicate_of_id``. An officer approves "
        "or rejects one proposed link; the decision and its author are appended "
        "to ``complaint_status_history``. A rejection changes nothing; an "
        "approval marks the complaint as a duplicate of the candidate."
    ),
)
def review_duplicate_link(
    complaint_id: uuid.UUID,
    payload: DuplicateReviewRequest,
    session: SessionDep,
    reviewer: OfficerUser,
) -> DuplicateReviewResult:
    if payload.candidate_id == complaint_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a complaint cannot be a duplicate of itself",
        )
    if payload.reviewer_id is not None and payload.reviewer_id != reviewer.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="the reviewer must be the authenticated officer",
        )

    try:
        complaint = session.get(Complaint, complaint_id)
        if complaint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"complaint {complaint_id} not found"
            )
        candidate = session.get(Complaint, payload.candidate_id)
        if candidate is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"complaint {payload.candidate_id} not found",
            )

        match = (
            _duplicate_agent().run(_snapshot_of(complaint), [_snapshot_of(candidate)]).matches[0]
        )
        previous_status = complaint.status
        approved = payload.decision == "approve"

        if approved:
            complaint.duplicate_of_id = candidate.id
            complaint.duplicate_similarity = match.match_score
            complaint.status = ComplaintStatus.DUPLICATE
            target_status = ComplaintStatus.DUPLICATE
            default_reason = "duplicate link approved by an officer"
        else:
            target_status = previous_status
            default_reason = "duplicate link rejected by an officer; the complaints stay separate"

        history = ComplaintStatusHistory(
            complaint_id=complaint.id,
            from_status=previous_status,
            to_status=target_status,
            changed_by_user_id=reviewer.id,
            reason=payload.reason or default_reason,
            notes=payload.notes
            or f"candidate {candidate.reference_id}; match score {match.match_score:.2f}",
        )
        session.add(history)
        session.commit()
        session.refresh(history)
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return DuplicateReviewResult(
        complaint_id=complaint.id,
        reference_id=complaint.reference_id,
        candidate_id=candidate.id,
        candidate_reference_id=candidate.reference_id,
        decision=payload.decision,
        linked=approved,
        match_score=match.match_score,
        text_similarity=match.text_similarity,
        explanation="; ".join(match.reasons),
        status=complaint.status,
        audit_recorded=True,
        audit_record_id=history.id,
        linking_requires_human_approval=True,
        message=(
            f"linked {complaint.reference_id} to {candidate.reference_id}"
            if approved
            else f"rejected the proposed link between {complaint.reference_id} "
            f"and {candidate.reference_id}; nothing changed"
        ),
    )


__all__ = [
    "DuplicateAnalyzeRequest",
    "DuplicateCandidateInput",
    "DuplicateMatchesPage",
    "DuplicateReviewRequest",
    "DuplicateReviewResult",
    "router",
]
