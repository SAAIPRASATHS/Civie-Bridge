"""The CivicBridge agent pipeline (Phases 3-4).

    Citizen Complaint (+ optional photo evidence, + stored complaints to compare)
          |
          v
    Intake Agent            ->  validated IntakeResult
          |
          v
    Classification Agent    ->  validated ClassificationResult
          |
          v
    Duplicate Detection     ->  ranked likely duplicates (recommendation only)
          |
          v
    Evidence Analysis       ->  what each image shows, and what it does not
          |
          v
    Final Complaint Analysis  (+ human review flag)
          |
          v
    optional persistence to ``complaints`` + ``complaint_evidence``
    + ``agent_execution_logs``

The duplicate and evidence agents are optional: they only run when the caller
supplies stored candidates or images, so the Phase 3 behaviour is unchanged for
a text-only complaint.

Model calls stay bounded. Intake and classification make one call each plus
possible schema-repair retries; duplicate detection calls the model at most once
and only for a borderline pair; evidence analysis calls a vision model only when
one is configured. Every agent degrades to deterministic output when the model
is unavailable, so a complaint is always analysed end to end.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy.orm import Session

from app.agents.classification import ClassificationAgent
from app.agents.duplicate import ComplaintSnapshot, DuplicateDetectionAgent
from app.agents.evidence import EvidenceAnalysisAgent, EvidenceImage
from app.agents.intake import IntakeAgent
from app.agents.llm_client import LlmClient
from app.agents.persistence import persist_analysis
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.models.complaint import Complaint
from app.schemas.agent import ComplaintAnalysis


@dataclass
class ComplaintPipeline:
    """Runs the intake, classification, duplicate and evidence agents in order."""

    intake: IntakeAgent
    classification: ClassificationAgent
    duplicate: DuplicateDetectionAgent
    evidence: EvidenceAnalysisAgent
    settings: Settings

    @classmethod
    def build(
        cls,
        *,
        settings: Settings | None = None,
        llm: LlmClient | None = None,
        use_llm: bool | None = None,
    ) -> ComplaintPipeline:
        """Create a pipeline whose agents share one LLM client and one cache."""
        cfg = settings or default_settings
        return cls(
            intake=IntakeAgent(settings=cfg, llm=llm, use_llm=use_llm),
            classification=ClassificationAgent(settings=cfg, llm=llm, use_llm=use_llm),
            duplicate=DuplicateDetectionAgent(settings=cfg, llm=llm, use_llm=use_llm),
            evidence=EvidenceAnalysisAgent(settings=cfg, use_llm=use_llm),
            settings=cfg,
        )

    def analyze(
        self,
        text: str,
        *,
        duplicate_candidates: Sequence[ComplaintSnapshot] | None = None,
        images: Sequence[EvidenceImage] | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        reference_id: str | None = None,
    ) -> ComplaintAnalysis:
        """Run the agents and return the combined, self-contained analysis.

        ``duplicate_candidates`` and ``images`` are optional; passing either
        adds that agent's step to ``steps`` and its result to the analysis.
        """
        started = time.perf_counter()
        intake = self.intake.run(text)
        classification = self.classification.run(intake)
        steps = ["intake", "classification"]
        llm_calls = int(intake.llm_used) + int(classification.llm_used)

        duplicate = None
        if duplicate_candidates:
            duplicate = self.duplicate.run_intake(
                intake,
                duplicate_candidates,
                classification=classification,
                reference_id=reference_id,
                latitude=latitude,
                longitude=longitude,
            )
            steps.append("duplicate_detection")
            llm_calls += int(duplicate.llm_used)

        evidence = []
        for image in images or []:
            result = self.evidence.run(
                image,
                category=classification.category,
                complaint_text=intake.normalized_description,
            )
            evidence.append(result)
            llm_calls += int(result.llm_used)
        if evidence:
            steps.append("evidence_verification")

        return ComplaintAnalysis(
            source_text=text,
            intake=intake,
            classification=classification,
            duplicate=duplicate,
            evidence=evidence,
            reference_id=reference_id,
            llm_calls=llm_calls,
            total_latency_ms=round((time.perf_counter() - started) * 1000, 1),
            steps=steps,
            generated_at=datetime.now(UTC),
        )

    def analyze_and_persist(
        self,
        text: str,
        session: Session,
        *,
        citizen_id: uuid.UUID | None = None,
        is_synthetic: bool = True,
        duplicate_candidates: Sequence[ComplaintSnapshot] | None = None,
        images: Sequence[EvidenceImage] | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> tuple[ComplaintAnalysis, Complaint]:
        """Analyse ``text``, persist it, and return the enriched analysis."""
        analysis = self.analyze(
            text,
            duplicate_candidates=duplicate_candidates,
            images=images,
            latitude=latitude,
            longitude=longitude,
        )
        complaint = persist_analysis(
            session, analysis, citizen_id=citizen_id, is_synthetic=is_synthetic
        )
        enriched = analysis.model_copy(
            update={
                "reference_id": complaint.reference_id,
                "complaint_id": str(complaint.id),
                "persisted": True,
                "steps": [*analysis.steps, "persisted"],
            }
        )
        return enriched, complaint


@lru_cache(maxsize=1)
def get_pipeline() -> ComplaintPipeline:
    """Process-wide pipeline so the Ollama model and response cache are shared."""
    return ComplaintPipeline.build()


def analyze_complaint(text: str, *, pipeline: ComplaintPipeline | None = None) -> ComplaintAnalysis:
    """Analyse one complaint without persisting it."""
    return (pipeline or get_pipeline()).analyze(text)


__all__ = ["ComplaintPipeline", "analyze_complaint", "get_pipeline"]
