"""Phase 5 routing and resolution endpoints and the officer assignment workflow.

    POST /api/routing/recommend                            -> RoutingRecommendation
    GET  /api/complaints/{complaint_id}/routing            -> RoutingRecommendation
    POST /api/complaints/{complaint_id}/routing/decision   -> RoutingDecisionResult
    POST /api/resolution/recommend                         -> ResolutionRecommendation

Two boundaries are enforced in code, not just documented:

* **The agents only recommend.** ``/routing/recommend``, the stored-complaint
  ``GET`` and ``/resolution/recommend`` never write anything - no assignment, no
  status change, no resolution.
* **A human decides.** ``complaints.current_department_id`` is written by exactly
  one code path in the application - the explicit decision call below - and only
  on an ``approve`` decision by an officer or administrator. Every decision, and
  every resolution recommendation, is recorded in ``complaint_status_history``;
  nothing here rejects, resolves or closes a complaint automatically.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agents.resolution import ResolutionAgent, ResolutionInput
from app.agents.routing import RoutingAgent, RoutingInput
from app.api.deps import OfficerUser, SessionDep
from app.core.logging import get_logger
from app.models.assignment import ComplaintAssignment
from app.models.complaint import Complaint
from app.models.department import Department
from app.models.enums import (
    AssignmentApprovalStatus,
    ComplaintCategory,
    ComplaintStatus,
)
from app.models.officer import Officer
from app.models.status_history import ComplaintStatusHistory
from app.schemas.agent import ResolutionRecommendation, RoutingRecommendation

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["routing"])

#: Both agents are deterministic and hold no model client, so one instance each
#: is enough for the whole process.
_ROUTING_AGENT = RoutingAgent()
_RESOLUTION_AGENT = ResolutionAgent()

#: Statuses a complaint may be moved out of when an officer approves a routing.
#: Terminal and already-assigned states are deliberately excluded so an approval
#: can never reopen or silently re-route a settled complaint.
ASSIGNABLE_FROM: frozenset[ComplaintStatus] = frozenset(
    {
        ComplaintStatus.SUBMITTED,
        ComplaintStatus.AWAITING_CLARIFICATION,
        ComplaintStatus.UNDER_REVIEW,
        ComplaintStatus.AWAITING_APPROVAL,
        ComplaintStatus.ESCALATED,
    }
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class RoutingRecommendRequest(BaseModel):
    """A complaint to route, supplied inline (no database access)."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, description="The complaint's wording, for context.")
    category: ComplaintCategory | None = Field(
        default=None, description="The complaint category; omitted means undetermined."
    )
    ward: str | None = None
    location_text: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)

    @model_validator(mode="after")
    def _coordinates_are_paired(self) -> RoutingRecommendRequest:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class ResolutionRecommendRequest(BaseModel):
    """A complaint to draft resolution steps for, supplied inline."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, description="The complaint's wording, for context.")
    category: ComplaintCategory | None = Field(
        default=None, description="The complaint category; omitted means undetermined."
    )
    ward: str | None = None
    available_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence kinds the caller already holds, matched against the guideline.",
    )


class RoutingDecisionRequest(BaseModel):
    """An officer's decision on a routing recommendation."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"] = Field(
        description="'approve' assigns the complaint; 'reject' leaves it unassigned."
    )
    reviewer_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional; when given it must equal the authenticated officer's id. "
            "The server always records the session's account for the audit trail."
        ),
    )
    officer_id: uuid.UUID | None = Field(
        default=None, description="Optionally assign a specific officer within the department."
    )
    reason: str | None = None
    notes: str | None = None


class RoutingDecisionResult(BaseModel):
    """The recorded outcome of an officer's routing decision."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: uuid.UUID
    reference_id: str
    decision: Literal["approve", "reject"]
    approval_status: AssignmentApprovalStatus
    department_code: str | None = None
    department_name: str | None = None

    assignment_id: uuid.UUID
    assigned: bool
    status: ComplaintStatus
    recommendation_confidence: float = Field(ge=0.0, le=1.0)

    audit_recorded: bool
    audit_record_id: uuid.UUID | None = None
    requires_human_approval: bool = True
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _database_unavailable(exc: SQLAlchemyError) -> HTTPException:
    logger.warning("Routing endpoint database error: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"database unavailable: {type(exc).__name__}",
    )


def _routing_input_from_complaint(complaint: Complaint) -> RoutingInput:
    return RoutingInput(
        text=complaint.normalized_description or complaint.description,
        category=complaint.category,
        ward=complaint.ward,
        location_text=complaint.location_text,
        latitude=complaint.latitude,
        longitude=complaint.longitude,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/routing/recommend",
    response_model=RoutingRecommendation,
    summary="Recommend a department for an inline complaint",
    description=(
        "Deterministic, rule-based routing over the prototype department "
        "directory. It returns the recommended department, the rule matches, "
        "any missing information and a review flag. It never assigns the "
        "complaint and never invents a department: an unmapped category yields "
        "no recommendation."
    ),
)
def recommend_routing(payload: RoutingRecommendRequest) -> RoutingRecommendation:
    return _ROUTING_AGENT.run(
        RoutingInput(
            text=payload.text,
            category=payload.category,
            ward=payload.ward,
            location_text=payload.location_text,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
    )


@router.get(
    "/complaints/{complaint_id}/routing",
    response_model=RoutingRecommendation,
    summary="Recommend a department for a stored complaint",
    description=(
        "Runs the routing agent against a stored complaint's category and "
        "location. Read-only: it neither assigns the complaint nor changes its "
        "status."
    ),
)
def get_routing_recommendation(
    complaint_id: uuid.UUID, session: SessionDep, officer: OfficerUser
) -> RoutingRecommendation:
    try:
        complaint = session.get(Complaint, complaint_id)
        if complaint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"complaint {complaint_id} not found"
            )
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return _ROUTING_AGENT.run(_routing_input_from_complaint(complaint))


@router.post(
    "/complaints/{complaint_id}/routing/decision",
    response_model=RoutingDecisionResult,
    summary="Approve or reject a routing recommendation (human decision)",
    description=(
        "The only code path that writes ``complaints.current_department_id``. An "
        "officer or administrator approves or rejects the routing "
        "recommendation; the recommendation, decision, reviewer, reason and "
        "timestamp are recorded in a ``complaint_assignments`` row and appended "
        "to ``complaint_status_history``. A rejection changes nothing. Neither "
        "outcome resolves, rejects or closes the complaint."
    ),
)
def decide_routing(
    complaint_id: uuid.UUID,
    payload: RoutingDecisionRequest,
    session: SessionDep,
    reviewer: OfficerUser,
) -> RoutingDecisionResult:
    try:
        if payload.reviewer_id is not None and payload.reviewer_id != reviewer.id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="the reviewer must be the authenticated officer",
            )

        complaint = session.get(Complaint, complaint_id)
        if complaint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"complaint {complaint_id} not found"
            )

        if payload.officer_id is not None and session.get(Officer, payload.officer_id) is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"officer {payload.officer_id} not found"
            )

        recommendation = _ROUTING_AGENT.run(_routing_input_from_complaint(complaint))
        if recommendation.recommended_department_code is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    "no department recommendation is available for this complaint; "
                    "it needs manual triage"
                ),
            )

        department = session.execute(
            select(Department).where(Department.code == recommendation.recommended_department_code)
        ).scalar_one_or_none()
        if department is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    f"recommended department {recommendation.recommended_department_code} "
                    "is not present in the directory"
                ),
            )

        approved = payload.decision == "approve"
        previous_status = complaint.status
        if approved:
            if previous_status not in ASSIGNABLE_FROM:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=(f"a complaint in status '{previous_status.value}' cannot be assigned"),
                )
            target_status = ComplaintStatus.ASSIGNED
        else:
            target_status = previous_status

        now = datetime.now(UTC)
        assignment = ComplaintAssignment(
            complaint_id=complaint.id,
            department_id=department.id,
            officer_id=payload.officer_id,
            ai_recommended_department_id=department.id,
            ai_confidence=recommendation.confidence,
            ai_explanation=recommendation.rationale,
            approval_status=(
                AssignmentApprovalStatus.APPROVED if approved else AssignmentApprovalStatus.REJECTED
            ),
            assigned_by_user_id=reviewer.id,
            approved_by_user_id=reviewer.id if approved else None,
            approved_at=now if approved else None,
            rejection_reason=(
                None
                if approved
                else (payload.reason or "routing recommendation rejected by an officer")
            ),
            notes=payload.notes,
        )
        session.add(assignment)

        if approved:
            complaint.current_department_id = department.id
            complaint.status = target_status

        decision_summary = (
            "routing recommendation approved by an officer"
            if approved
            else "routing recommendation rejected by an officer; the complaint is unchanged"
        )
        history = ComplaintStatusHistory(
            complaint_id=complaint.id,
            from_status=previous_status,
            to_status=target_status,
            changed_by_user_id=reviewer.id,
            reason=(
                f"{decision_summary}: {payload.reason}" if payload.reason else decision_summary
            ),
            notes=payload.notes
            or f"department {department.code}; routing confidence {recommendation.confidence:.2f}",
        )
        session.add(history)
        session.commit()
        session.refresh(assignment)
        session.refresh(history)
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_unavailable(exc) from exc

    return RoutingDecisionResult(
        complaint_id=complaint.id,
        reference_id=complaint.reference_id,
        decision=payload.decision,
        approval_status=assignment.approval_status,
        department_code=department.code,
        department_name=department.name,
        assignment_id=assignment.id,
        assigned=approved,
        status=complaint.status,
        recommendation_confidence=recommendation.confidence,
        audit_recorded=True,
        audit_record_id=history.id,
        requires_human_approval=True,
        message=(
            f"assigned {complaint.reference_id} to {department.name} ({department.code}); "
            f"status is now '{complaint.status.value}'"
            if approved
            else f"rejected the routing recommendation for {complaint.reference_id}; "
            "the complaint is unchanged"
        ),
    )


@router.post(
    "/resolution/recommend",
    response_model=ResolutionRecommendation,
    summary="Recommend resolution steps for an inline complaint",
    description=(
        "Quotes the prototype service guideline for the complaint's category and "
        "returns suggested next steps, the escalation ladder and the required "
        "evidence. SLA durations are always returned as unverified prototype "
        "estimates. It is recommendation-only: nothing is resolved, rejected or "
        "closed, and an officer must approve any status change."
    ),
)
def recommend_resolution(payload: ResolutionRecommendRequest) -> ResolutionRecommendation:
    return _RESOLUTION_AGENT.run(
        ResolutionInput(
            text=payload.text,
            category=payload.category,
            ward=payload.ward,
            available_evidence=tuple(payload.available_evidence),
        )
    )


__all__ = [
    "ASSIGNABLE_FROM",
    "ResolutionRecommendRequest",
    "RoutingDecisionRequest",
    "RoutingDecisionResult",
    "RoutingRecommendRequest",
    "router",
]
