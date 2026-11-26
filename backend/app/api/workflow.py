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

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.duplicate import ComplaintSnapshot
from app.agents.evidence import EvidenceImage
from app.agents.workflow import WorkflowInputs, get_workflow
from app.api.deps import AuthenticatedUser, OfficerUser
from app.core.config import settings
from app.core.logging import get_logger
from app.core.uploads import UploadValidationError, validate_image_upload
from app.schemas.agent import ComplaintCategory
from app.schemas.workflow import WorkflowRunResult

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/workflow", tags=["workflow"])

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
        try:
            validated = validate_image_upload(
                self.file_name, content, declared_type=self.content_type
            )
        except UploadValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"the uploaded file {self.file_name!r} was rejected: {exc}",
            ) from exc
        return EvidenceImage.from_bytes(validated.file_name, validated.content)


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

    The reviewer is resolved from the authenticated session, never trusted from
    the body: ``reviewer_id`` is accepted only as a consistency check against
    the signed-in caller. The note is an acknowledgement only; it authorises no
    consequential action.
    """

    model_config = ConfigDict(extra="forbid")

    reviewer_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional; when given it must equal the authenticated officer's id. "
            "The server always records the session's account as the reviewer."
        ),
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
        "rejection and closure all remain human decisions. Requires a signed-in "
        "account; the citizen and officer portals both use this endpoint."
    ),
)
def run_workflow(payload: WorkflowRunRequest, user: AuthenticatedUser) -> WorkflowRunResult:
    _ensure_enabled()
    logger.info("Workflow run requested by account %s", user.id)
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
        "the assembled result. Only an officer or administrator may resume; a "
        "citizen is refused by the server before the handler runs. Resuming "
        "records an acknowledgement only - it never authorises an assignment, a "
        "duplicate merge, a rejection, a resolution or a closure."
    ),
)
def resume_workflow(
    workflow_run_id: str, payload: WorkflowResumeRequest, reviewer: OfficerUser
) -> WorkflowRunResult:
    _ensure_enabled()
    if payload.reviewer_id is not None and payload.reviewer_id != reviewer.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="the reviewer must be the authenticated officer",
        )
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
