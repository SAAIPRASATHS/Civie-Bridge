"""Database integration for the Phase 3 agents.

Persists an analysis against the **existing** ``complaints`` table and records
one ``agent_execution_logs`` row per agent step. No migration is added: every
column written here already exists from Phase 2, and the values that come from
the agents land in the dedicated ``classification_*`` / ``urgency_score``
columns so they stay distinguishable from citizen-supplied facts.

The module never opens its own transaction - it uses the caller's
:class:`~sqlalchemy.orm.Session`, so a CLI run and an API request share the
same commit/rollback behaviour.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.agent_execution_log import AgentExecutionLog
from app.models.complaint import Complaint
from app.models.department import Department
from app.models.enums import AgentExecutionStatus, AgentName, ComplaintStatus
from app.schemas.agent import (
    ClassificationResult,
    ComplaintAnalysis,
    DuplicateDetectionResult,
    EvidenceAnalysisResult,
    IntakeResult,
)

logger = get_logger(__name__)

#: Reference ids for live submissions are visually distinct from the seeded
#: ``CB-<year>-000123`` dataset ids so a demo record can never be mistaken for
#: a synthetic dataset row.
REFERENCE_ID_PREFIX = "CB"
REFERENCE_ID_MARKER = "L"


def generate_reference_id(session: Session) -> str:
    """Allocate a complaint reference id that is not already in the database."""
    year = datetime.now(UTC).year
    for _ in range(20):
        candidate = (
            f"{REFERENCE_ID_PREFIX}-{year}-{REFERENCE_ID_MARKER}{uuid.uuid4().hex[:6].upper()}"
        )
        exists = session.execute(
            select(Complaint.id).where(Complaint.reference_id == candidate)
        ).first()
        if exists is None:
            return candidate
    raise RuntimeError("could not allocate a unique complaint reference id")


def _build_log(
    *,
    agent: AgentName,
    step_index: int,
    input_payload: dict,
    output_payload: dict,
    explanation: str,
    confidence: float,
    duration_ms: float | None,
    model_name: str | None,
    status: AgentExecutionStatus,
    workflow_run_id: str | None = None,
) -> AgentExecutionLog:
    completed_at = datetime.now(UTC)
    started_at = completed_at - timedelta(milliseconds=duration_ms) if duration_ms else completed_at
    return AgentExecutionLog(
        workflow_run_id=workflow_run_id,
        agent_name=agent,
        step_index=step_index,
        status=status,
        input_payload=input_payload,
        output_payload=output_payload,
        explanation=explanation,
        confidence=round(confidence, 3),
        model_name=model_name,
        duration_ms=int(duration_ms) if duration_ms else None,
        started_at=started_at,
        completed_at=completed_at,
    )


def _intake_log(intake: IntakeResult, workflow_run_id: str | None = None) -> AgentExecutionLog:
    return _build_log(
        agent=AgentName.INTAKE,
        step_index=0,
        input_payload={"text": intake.original_text},
        output_payload=intake.model_dump(mode="json"),
        explanation=intake.reasoning,
        confidence=intake.confidence,
        duration_ms=intake.latency_ms,
        model_name=intake.model_name,
        status=AgentExecutionStatus.SUCCEEDED,
        workflow_run_id=workflow_run_id,
    )


def _classification_log(
    classification: ClassificationResult, workflow_run_id: str | None = None
) -> AgentExecutionLog:
    return _build_log(
        agent=AgentName.CLASSIFICATION,
        step_index=1,
        input_payload={
            "category_hint": classification.matched_signals,
            "department_code": classification.department_code,
        },
        output_payload=classification.model_dump(mode="json"),
        explanation=classification.reasoning,
        confidence=classification.confidence,
        duration_ms=classification.latency_ms,
        model_name=classification.model_name,
        status=(
            AgentExecutionStatus.AWAITING_HUMAN
            if classification.needs_human_review
            else AgentExecutionStatus.SUCCEEDED
        ),
        workflow_run_id=workflow_run_id,
    )


def _status_for(intake: IntakeResult, classification: ClassificationResult) -> ComplaintStatus:
    if intake.needs_clarification:
        return ComplaintStatus.AWAITING_CLARIFICATION
    if classification.needs_human_review:
        return ComplaintStatus.UNDER_REVIEW
    return ComplaintStatus.SUBMITTED


def _duplicate_log(
    duplicate: DuplicateDetectionResult,
    step_index: int,
    workflow_run_id: str | None = None,
) -> AgentExecutionLog:
    """Record the duplicate step. A likely duplicate is queued for a human, never merged."""
    return _build_log(
        agent=AgentName.DUPLICATE_DETECTION,
        step_index=step_index,
        input_payload={
            "subject_reference_id": duplicate.subject_reference_id,
            "candidates_considered": duplicate.candidates_considered,
        },
        output_payload=duplicate.model_dump(mode="json"),
        explanation=duplicate.reasoning,
        confidence=duplicate.confidence,
        duration_ms=duplicate.latency_ms,
        model_name=duplicate.model_name,
        status=(
            AgentExecutionStatus.AWAITING_HUMAN
            if duplicate.likely_duplicate_found
            else AgentExecutionStatus.SUCCEEDED
        ),
        workflow_run_id=workflow_run_id,
    )


def _evidence_log(
    evidence: EvidenceAnalysisResult,
    step_index: int,
    workflow_run_id: str | None = None,
) -> AgentExecutionLog:
    """Record one evidence step. Evidence always stays pending a human decision."""
    return _build_log(
        agent=AgentName.EVIDENCE_VERIFICATION,
        step_index=step_index,
        input_payload={
            "file_name": evidence.file_name,
            "content_digest": evidence.content_digest,
            "category": evidence.category.value if evidence.category else None,
        },
        output_payload=evidence.model_dump(mode="json"),
        explanation=evidence.reasoning,
        confidence=evidence.confidence,
        duration_ms=evidence.latency_ms,
        model_name=evidence.model_name,
        # The agent never validates or rejects evidence, so it always awaits a human.
        status=AgentExecutionStatus.AWAITING_HUMAN,
        workflow_run_id=workflow_run_id,
    )


def persist_analysis(
    session: Session,
    analysis: ComplaintAnalysis,
    *,
    citizen_id: uuid.UUID | None = None,
    is_synthetic: bool = True,
    reference_id: str | None = None,
    workflow_run_id: str | None = None,
) -> Complaint:
    """Write ``analysis`` as one complaint plus its two agent execution logs."""

    intake = analysis.intake
    classification = analysis.classification

    department_id: uuid.UUID | None = None
    if classification.department_code:
        department_id = session.execute(
            select(Department.id).where(Department.code == classification.department_code)
        ).scalar_one_or_none()

    complaint = Complaint(
        reference_id=reference_id or generate_reference_id(session),
        citizen_id=citizen_id,
        description=intake.original_text,
        normalized_description=intake.normalized_description,
        language=intake.language,
        location_text=intake.location_text,
        ward=intake.ward,
        status=_status_for(intake, classification),
        category=classification.category,
        priority=classification.priority,
        current_department_id=department_id,
        classification_confidence=classification.confidence,
        classification_explanation=classification.reasoning,
        urgency_score=classification.urgency_score,
        needs_clarification=intake.needs_clarification,
        clarification_question=" ".join(intake.clarification_questions) or None,
        is_synthetic=is_synthetic,
    )
    session.add(complaint)
    session.flush()

    logs = [
        _intake_log(intake, workflow_run_id),
        _classification_log(classification, workflow_run_id),
    ]
    if analysis.duplicate is not None:
        logs.append(_duplicate_log(analysis.duplicate, len(logs), workflow_run_id))
    for evidence in analysis.evidence:
        logs.append(_evidence_log(evidence, len(logs), workflow_run_id))

    for log in logs:
        log.complaint_id = complaint.id
        session.add(log)
    session.flush()

    logger.info(
        "Persisted complaint %s (category=%s, priority=%s, needs_review=%s)",
        complaint.reference_id,
        classification.category.value if classification.category else "undetermined",
        classification.priority.value,
        classification.needs_human_review,
    )
    return complaint


__all__ = ["REFERENCE_ID_PREFIX", "generate_reference_id", "persist_analysis"]
