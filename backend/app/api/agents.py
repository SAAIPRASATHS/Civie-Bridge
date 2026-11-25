"""Phase 3 agent endpoints.

    POST /api/agents/intake              -> IntakeResult
    POST /api/agents/classify            -> ClassificationResult
    POST /api/agents/analyze-complaint   -> ComplaintAnalysis (optionally persisted)

``classify`` accepts either raw ``text`` (the intake agent runs first) or an
already-computed ``intake`` object, so a caller that has just called
``/intake`` does not pay for the same model call twice.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.pipeline import get_pipeline
from app.core.logging import get_logger
from app.database.session import get_session
from app.schemas.agent import ClassificationResult, ComplaintAnalysis, IntakeResult

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

#: ``Annotated`` keeps the ``Depends`` call out of an argument default (ruff B008).
SessionDep = Annotated[Session, Depends(get_session)]


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The citizen's complaint, verbatim.")


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, description="Raw complaint text.")
    intake: IntakeResult | None = Field(
        default=None, description="A previous /intake result, to avoid a duplicate model call."
    )

    @model_validator(mode="after")
    def _require_one_input(self) -> ClassifyRequest:
        if not (self.text and self.text.strip()) and self.intake is None:
            raise ValueError("provide either 'text' or 'intake'")
        return self


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The citizen's complaint, verbatim.")
    persist: bool = Field(
        default=False, description="Write the analysis to the complaints table when true."
    )
    citizen_id: uuid.UUID | None = None
    is_synthetic: bool = Field(
        default=True, description="Prototype flag stored on the complaint row."
    )


def _intake(text: str) -> IntakeResult:
    try:
        return get_pipeline().intake.run(text)
    except ValueError as exc:  # empty/whitespace-only text
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post(
    "/intake",
    response_model=IntakeResult,
    summary="Complaint Intake Agent",
    description=(
        "Understands English, Tamil and Tamil-transliterated complaints and returns "
        "validated structured JSON. Deterministic extraction runs first; the local "
        "model enriches it and every model claim is checked against the citizen's words."
    ),
)
def run_intake(payload: IntakeRequest) -> IntakeResult:
    return _intake(payload.text)


@router.post(
    "/classify",
    response_model=ClassificationResult,
    summary="Classification Agent",
    description=(
        "Assigns one of the four supported categories, a priority and a department. "
        "The department is read from the prototype department directory - the model "
        "cannot invent one. Low confidence flags the complaint for human review."
    ),
)
def run_classification(payload: ClassifyRequest) -> ClassificationResult:
    pipeline = get_pipeline()
    intake = payload.intake if payload.intake is not None else _intake(payload.text or "")
    return pipeline.classification.run(intake)


@router.post(
    "/analyze-complaint",
    response_model=ComplaintAnalysis,
    summary="Run the full intake + classification pipeline",
    description=(
        "Runs both agents and returns their validated results together. With "
        "``persist=true`` the analysis is also written to the complaints table with "
        "one agent_execution_logs row per step."
    ),
)
def analyze_complaint(payload: AnalyzeRequest, session: SessionDep) -> ComplaintAnalysis:
    pipeline = get_pipeline()

    if not payload.persist:
        return pipeline.analyze(payload.text)

    try:
        analysis, _ = pipeline.analyze_and_persist(
            payload.text,
            session,
            citizen_id=payload.citizen_id,
            is_synthetic=payload.is_synthetic,
        )
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("Persistence failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unavailable: {type(exc).__name__}",
        ) from exc
    return analysis


__all__ = ["router"]
