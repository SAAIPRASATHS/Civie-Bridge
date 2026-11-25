"""Phase 6 orchestration endpoints.

    POST /api/v1/workflow/run                          -> WorkflowRunResult
    POST /api/v1/workflow/{workflow_run_id}/resume     -> WorkflowRunResult

One call drives the whole LangGraph workflow: intake, classification, duplicate
detection, optional evidence analysis, routing and resolution recommendation,
then a human-review checkpoint and a final, assembled result. The endpoint
reuses every existing agent and schema unchanged.

Two safety properties hold here exactly as they do inside the graph:

* **Nothing is decided.** The workflow assigns no department, links no duplicate,
  resolves, rejects or closes nothing and never writes a status-history row. It
  returns recommendations plus the review requirements; the Phase 4/5 officer
  endpoints remain the only paths that act on a complaint.
* **A resume is not an authorisation.** ``/resume`` validates that the caller is
  a real officer or administrator, then records an acknowledgement and returns
  the assembled result. It grants no permission to assign, merge, reject,
  resolve or close anything.

Agents that fail or time out degrade to the deterministic fallbacks the earlier
phases already allow; a run never fails just because the model is unavailable.
"""

from __future__ import annotations

import base64
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.duplicate import ComplaintSnapshot
from app.agents.evidence import EvidenceImage
from app.agents.workflow import WorkflowInputs, get_workflow
from app.core.config import settings
from app.core.logging import get_logger
from app.database.session import get_session
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.agent import ComplaintCategory
from app.schemas.workflow import WorkflowRunResult

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/workflow", tags=["workflow"])

#: ``Annotated`` keeps the ``Depends`` call out of an argument default (ruff B008).
SessionDep = Annotated[Session, Depends(get_session)]

#: Upper bound on the base64 payload of one image, so a request cannot exhaust
#: memory before the evidence agent ever sees it (~8 MB of raw bytes).
MAX_IMAGE_BASE64_CHARS = 12_000_000


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class WorkflowCandidateInput(BaseModel):
    """A stored-style complaint supplied inline, as a duplicate comparison set."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The candidate complaint's wording.")
    reference_id: str | None = None
    category: ComplaintCategory | None = None
    ward: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)

    @model_validator(mode="after")
    def _coordinates_are_paired(self) -> WorkflowCandidateInput:
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


class WorkflowImageInput(BaseModel):
    """One evidence image supplied inline as base64 bytes."""

    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, description="Original file name, kept verbatim.")
    content_base64: str = Field(
        min_length=1,
        max_length=MAX_IMAGE_BASE64_CHARS,
        description="The image bytes, base64-encoded (no data: prefix).",
    )
    content_type: str | None = None

    def to_image(self) -> EvidenceImage:
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"content_base64 for {self.file_name!r} is not valid base64 data",
            ) from exc
        return EvidenceImage.from_bytes(self.file_name, content)


class WorkflowRunRequest(BaseModel):
    """A complaint to run through the full orchestration workflow."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The citizen's complaint, verbatim.")
    request_id: str | None = Field(
        default=None, description="Caller-supplied idempotency key; also used as the run id."
    )
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)

    candidates: list[WorkflowCandidateInput] = Field(
        default_factory=list,
        description="Stored-style complaints to compare against; empty skips duplicate detection.",
    )
    images: list[WorkflowImageInput] = Field(
        default_factory=list,
        description="Evidence images to analyse; empty skips the evidence node.",
    )

    interrupt_for_review: bool = Field(
        default=False,
        description=(
            "When true the run pauses at the human-review node and is resumed via "
            "/resume. The pause is an acknowledgement request only."
        ),
    )
    persist: bool = Field(
        default=False,
        description="Write the complaint and its step logs once, after the run completes.",
    )

    @model_validator(mode="after")
    def _coordinates_are_paired(self) -> WorkflowRunRequest:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self

    def to_inputs(self) -> WorkflowInputs:
        return WorkflowInputs(
            candidates=[candidate.to_snapshot() for candidate in self.candidates],
            images=[image.to_image() for image in self.images],
            interrupt_for_review=self.interrupt_for_review,
            persist=self.persist,
        )


class WorkflowResumeRequest(BaseModel):
    """An officer's acknowledgement of a paused run.

    The reviewer is validated as a real officer or administrator. The note is
    recorded as an acknowledgement only; it authorises no consequential action.
    """

    model_config = ConfigDict(extra="forbid")

    reviewer_id: uuid.UUID = Field(
        description="The officer or administrator acknowledging the review checkpoint."
    )
    note: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_enabled() -> None:
    if not settings.orchestration_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the orchestration workflow is disabled by configuration",
        )


def _authorise_reviewer(session: Session, reviewer_id: uuid.UUID) -> User:
    """Confirm the reviewer exists and may approve; a citizen may not."""
    try:
        reviewer = session.get(User, reviewer_id)
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("Workflow resume database error: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unavailable: {type(exc).__name__}",
        ) from exc
    if reviewer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"reviewer {reviewer_id} not found")
    if reviewer.role is UserRole.CITIZEN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="only an officer or administrator may acknowledge a review checkpoint",
        )
    return reviewer


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    response_model=WorkflowRunResult,
    summary="Run the full orchestration workflow for one complaint",
    description=(
        "Drives intake, classification, duplicate detection, optional evidence "
        "analysis, routing and resolution recommendation through one LangGraph "
        "workflow and returns every validated agent output, the potential "
        "duplicate matches, the recommendations, the workflow status, the review "
        "requirements and any degraded-mode details. The workflow performs no "
        "consequential action: assignment, duplicate linking, resolution, "
        "rejection and closure all remain human decisions."
    ),
)
def run_workflow(payload: WorkflowRunRequest) -> WorkflowRunResult:
    _ensure_enabled()
    inputs = payload.to_inputs()
    workflow = get_workflow()
    return workflow.invoke(
        text=payload.text,
        inputs=inputs,
        request_id=payload.request_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )


@router.post(
    "/{workflow_run_id}/resume",
    response_model=WorkflowRunResult,
    summary="Resume a paused workflow run (human acknowledgement)",
    description=(
        "Resumes a run that paused at the human-review checkpoint and returns "
        "the assembled result. The reviewer is validated as an officer or "
        "administrator; a citizen is refused. Resuming records an "
        "acknowledgement only - it never authorises an assignment, a duplicate "
        "merge, a rejection, a resolution or a closure."
    ),
)
def resume_workflow(
    workflow_run_id: str, payload: WorkflowResumeRequest, session: SessionDep
) -> WorkflowRunResult:
    _ensure_enabled()
    reviewer = _authorise_reviewer(session, payload.reviewer_id)
    workflow = get_workflow()
    return workflow.resume(
        workflow_run_id=workflow_run_id,
        reviewer_id=str(reviewer.id),
        note=payload.note,
    )


__all__ = [
    "WorkflowCandidateInput",
    "WorkflowImageInput",
    "WorkflowResumeRequest",
    "WorkflowRunRequest",
    "router",
]
