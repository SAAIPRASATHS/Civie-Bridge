"""Phase 4 evidence-analysis endpoint.

    POST /api/evidence/analyze  -> EvidenceAnalysisResponse

The endpoint runs the existing :class:`~app.agents.evidence.EvidenceAnalysisAgent`
over one uploaded image and returns what the measurement layer can honestly
establish - and, just as importantly, what it cannot. Three properties are
structural and mirrored by the response schema:

* the original evidence is never altered; the analysis carries the file's
  SHA-256 digest so the stored record can be tied back to the exact bytes,
* ``is_proof_of_validity`` is always ``false`` and ``verification_status`` is
  capped at ``uncertain`` (or ``manual_review_required``), so a photo can never
  validate or reject a complaint on its own,
* ``requires_manual_review`` is always ``true``.

The endpoint performs measurements only. It does not claim any object, text or
scene recognition: with no vision model configured, the result says so in its
``limitations``. When a ``complaint_id`` is supplied the analysis is attached to
that stored complaint as a ``complaint_evidence`` row; this is optional, and a
compliant request must name where the original file is kept
(``storage_reference``) so nothing is invented.
"""

from __future__ import annotations

import base64
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.evidence import EvidenceAnalysisAgent, EvidenceImage
from app.agents.pipeline import get_pipeline
from app.core.logging import get_logger
from app.database.session import get_session
from app.models.complaint import Complaint
from app.models.complaint_evidence import ComplaintEvidence
from app.models.enums import ComplaintCategory
from app.schemas.agent import EvidenceAnalysisResult

logger = get_logger(__name__)

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

#: ``Annotated`` keeps the ``Depends`` call out of an argument default (ruff B008).
SessionDep = Annotated[Session, Depends(get_session)]


class EvidenceAnalyzeRequest(BaseModel):
    """One image to analyse, optionally attached to a stored complaint."""

    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, description="Original file name, kept verbatim.")
    content_base64: str = Field(
        min_length=1, description="The image bytes, base64-encoded (no data: prefix)."
    )
    content_type: str | None = Field(default=None, description="MIME type, if the caller knows it.")
    category: ComplaintCategory | None = Field(
        default=None, description="The complaint's category, for category-specific cues."
    )
    complaint_text: str | None = Field(
        default=None, description="The complaint's wording, for context only."
    )

    complaint_id: uuid.UUID | None = Field(
        default=None, description="Attach the analysis to this stored complaint when given."
    )
    storage_reference: str | None = Field(
        default=None,
        description="Where the original file is kept; required when attaching to a complaint.",
    )

    @model_validator(mode="after")
    def _require_storage_reference(self) -> EvidenceAnalyzeRequest:
        if self.complaint_id is not None and not (self.storage_reference or "").strip():
            raise ValueError(
                "storage_reference is required when attaching evidence to a stored complaint"
            )
        return self


class EvidenceAnalysisResponse(BaseModel):
    """The evidence analysis, plus whether it was stored against a complaint."""

    model_config = ConfigDict(extra="forbid")

    analysis: EvidenceAnalysisResult

    complaint_id: uuid.UUID | None = None
    evidence_id: uuid.UUID | None = None
    persisted: bool = False
    original_evidence_preserved: bool = True
    message: str


def _evidence_agent() -> EvidenceAnalysisAgent:
    """The shared pipeline's evidence agent."""
    return get_pipeline().evidence


@router.post(
    "/analyze",
    response_model=EvidenceAnalysisResponse,
    summary="Analyse one evidence image",
    description=(
        "Measures an uploaded image with OpenCV (sharpness, exposure, contrast, "
        "edge density, colour) and reports observable findings separately from "
        "low-confidence interpretations. It never validates or rejects a "
        "complaint and never performs object or text recognition."
    ),
)
def analyze_evidence(
    payload: EvidenceAnalyzeRequest, session: SessionDep
) -> EvidenceAnalysisResponse:
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="content_base64 is not valid base64-encoded data",
        ) from exc

    image = EvidenceImage.from_bytes(payload.file_name, content)
    analysis = _evidence_agent().run(
        image, category=payload.category, complaint_text=payload.complaint_text
    )

    if payload.complaint_id is None:
        return EvidenceAnalysisResponse(
            analysis=analysis,
            message="analysed the image; nothing was stored because no complaint_id was given",
        )

    try:
        complaint = session.get(Complaint, payload.complaint_id)
        if complaint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"complaint {payload.complaint_id} not found",
            )

        evidence = ComplaintEvidence(
            complaint_id=complaint.id,
            file_name=payload.file_name,
            storage_path=payload.storage_reference or payload.file_name,
            mime_type=payload.content_type,
            file_size_bytes=len(content) or None,
            caption=payload.complaint_text,
            verification_status=analysis.verification_status,
            relevance_score=analysis.relevance_score,
            requires_manual_review=analysis.requires_manual_review,
            verification_notes=analysis.verification_notes,
            agent_explanation=analysis.reasoning,
            analysis_model=analysis.model_name,
        )
        session.add(evidence)
        session.commit()
        session.refresh(evidence)
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("Evidence persistence failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unavailable: {type(exc).__name__}",
        ) from exc

    return EvidenceAnalysisResponse(
        analysis=analysis,
        complaint_id=complaint.id,
        evidence_id=evidence.id,
        persisted=True,
        message=(
            f"stored the analysis against {complaint.reference_id}; the complaint "
            "status was not changed and a human still reviews the evidence"
        ),
    )


__all__ = [
    "EvidenceAnalysisResponse",
    "EvidenceAnalyzeRequest",
    "router",
]
