"""Phase 3 unit tests for the Classification Agent.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_classification_agent.py -v

The model is replaced by a **scripted fake LLM client**, so every assertion is
deterministic. Tests that exercise the real ``llama3.1:latest`` model live in
``test_phase3_integration.py`` and skip automatically when Ollama is down.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.classification import URGENCY_CRITICAL_AT, ClassificationAgent
from app.agents.errors import AgentLlmError
from app.agents.llm_client import LlmCallResult
from app.core.config import Settings
from app.models.enums import ComplaintCategory, ComplaintPriority
from app.schemas.agent import ClassificationResult

WASTE_COMPLAINT = "For the past five days garbage has not been collected near our street."
ROAD_COMPLAINT = "Large pothole on the main road near the bus stop."
WATER_COMPLAINT = "No water supply in Ward 10 for two days."
LIGHTING_COMPLAINT = "The street light near the park is not working for one week."
UNDETERMINED_COMPLAINT = "Something is wrong in our area please fix it soon"


class ScriptedLlm:
    """Fake ``LlmClient`` returning pre-scripted responses in order."""

    def __init__(self, responses: list[str] | None = None, *, available: bool = True) -> None:
        self.responses = list(responses or [])
        self.available = available
        self.calls: list[dict[str, str]] = []

    def is_available(self) -> bool:
        return self.available

    def complete(self, *, system: str, user: str) -> LlmCallResult:
        self.calls.append({"system": system, "user": user})
        if not self.responses:
            raise AgentLlmError("no scripted response left")
        return LlmCallResult(
            text=self.responses.pop(0),
            model="scripted-fake",
            provider="fake",
            latency_ms=1.0,
        )


def _payload(**overrides: object) -> str:
    """A schema-valid classification draft, with targeted fields overridden."""
    draft: dict[str, object] = {
        "category": "waste_management",
        "subcategory": "garbage_not_collected",
        "priority": "high",
        "urgency_score": 0.6,
        "confidence": 0.85,
        "reasoning": "The complaint states garbage has not been collected for five days.",
        "recommended_next_action": "Dispatch the collection beat to the reported street.",
    }
    draft.update(overrides)
    return json.dumps(draft)


# ---------------------------------------------------------------------------
# 6-9. The four supported categories (deterministic, no model)
# ---------------------------------------------------------------------------


def test_waste_management_classification() -> None:
    result = ClassificationAgent(use_llm=False).run(WASTE_COMPLAINT)

    assert isinstance(result, ClassificationResult)
    assert result.category is ComplaintCategory.WASTE_MANAGEMENT
    assert result.subcategory == "garbage_not_collected"
    assert result.priority is ComplaintPriority.HIGH
    assert result.department_code == "SWM"
    assert result.guideline_code == "GDL-SWM-001"
    assert result.sla_hours == 48
    assert result.needs_human_review is False


def test_road_maintenance_classification() -> None:
    result = ClassificationAgent(use_llm=False).run(ROAD_COMPLAINT)

    assert result.category is ComplaintCategory.ROAD_MAINTENANCE
    assert result.subcategory == "pothole"
    assert result.priority is ComplaintPriority.HIGH
    assert result.department_code == "RIM"
    assert result.sla_hours == 72


def test_water_supply_classification() -> None:
    result = ClassificationAgent(use_llm=False).run(WATER_COMPLAINT)

    assert result.category is ComplaintCategory.WATER_SUPPLY
    assert result.priority is ComplaintPriority.HIGH
    assert result.department_code == "WSS"
    assert result.sla_hours == 24


def test_street_lighting_classification() -> None:
    result = ClassificationAgent(use_llm=False).run(LIGHTING_COMPLAINT)

    assert result.category is ComplaintCategory.STREET_LIGHTING
    assert result.subcategory == "light_not_working"
    assert result.priority is ComplaintPriority.MEDIUM
    assert result.department_code == "SLE"
    assert result.sla_hours == 48


def test_deterministic_routing_is_consistent_and_flagged_when_uncertain() -> None:
    result = ClassificationAgent(use_llm=False).run(UNDETERMINED_COMPLAINT)

    assert result.category is None
    assert result.department_code is None
    assert result.needs_human_review is True
    assert result.review_reason
    assert "human officer" in result.recommended_next_action


def test_empty_complaint_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ClassificationAgent(use_llm=False).run("  ")


# ---------------------------------------------------------------------------
# 10. The model may not invent a department
# ---------------------------------------------------------------------------


def test_department_comes_from_the_directory_not_the_model() -> None:
    fake = ScriptedLlm(
        [
            _payload(
                department_code="FAKE-DEPT",
                department_name="Model Invented Department",
                sla_hours=1,
            )
        ]
    )

    result = ClassificationAgent(llm=fake, use_llm=True).run(WASTE_COMPLAINT)

    assert result.llm_used is True
    assert result.department_code == "SWM"
    assert result.department_name == "Municipal Solid Waste Management Department"
    assert result.guideline_code == "GDL-SWM-001"
    assert result.sla_hours == 48
    assert result.routing_source == "prototype_department_directory"


# ---------------------------------------------------------------------------
# 11. Invalid / unsupported model output
# ---------------------------------------------------------------------------


def test_unsupported_model_category_is_repaired() -> None:
    fake = ScriptedLlm(
        [
            _payload(category="garbage_collection"),
            _payload(category="waste_management"),
        ]
    )

    result = ClassificationAgent(llm=fake, use_llm=True).run(WASTE_COMPLAINT)

    assert len(fake.calls) == 2
    assert result.category is ComplaintCategory.WASTE_MANAGEMENT
    assert result.degraded is False


def test_invalid_model_output_falls_back_to_keywords() -> None:
    fake = ScriptedLlm(["not json", "still not json"])

    result = ClassificationAgent(llm=fake, use_llm=True).run(WASTE_COMPLAINT)

    assert result.degraded is True
    assert result.llm_used is False
    assert result.category is ComplaintCategory.WASTE_MANAGEMENT
    assert len(fake.calls) == 2


def test_unavailable_model_uses_keywords_without_calling_it() -> None:
    fake = ScriptedLlm([], available=False)

    result = ClassificationAgent(llm=fake, use_llm=True).run(WATER_COMPLAINT)

    assert result.degraded is True
    assert result.llm_used is False
    assert fake.calls == []
    assert result.category is ComplaintCategory.WATER_SUPPLY


# ---------------------------------------------------------------------------
# 12. Low confidence and model/deterministic disagreement -> human review
# ---------------------------------------------------------------------------


def test_low_confidence_is_flagged_for_human_review() -> None:
    settings = Settings(agent_confidence_threshold=0.95)

    result = ClassificationAgent(settings=settings, use_llm=False).run(WASTE_COMPLAINT)

    assert result.confidence < 0.95
    assert result.needs_human_review is True
    assert result.review_reason is not None
    assert "threshold" in result.review_reason


def test_confident_confidence_passes_the_default_threshold() -> None:
    result = ClassificationAgent(use_llm=False).run(WASTE_COMPLAINT)

    assert result.confidence >= 0.60
    assert result.needs_human_review is False
    assert result.review_reason is None


def test_weak_model_category_agreement_escalates_to_human_review() -> None:
    fake = ScriptedLlm([_payload(category="road_maintenance", confidence=0.4)])

    result = ClassificationAgent(llm=fake, use_llm=True).run(WASTE_COMPLAINT)

    assert result.category is ComplaintCategory.WASTE_MANAGEMENT
    assert result.needs_human_review is True
    assert result.review_reason is not None
    assert "deterministic" in result.review_reason


def test_confident_model_disagreement_still_escalates_to_human_review() -> None:
    fake = ScriptedLlm([_payload(category="road_maintenance", confidence=0.95)])

    result = ClassificationAgent(llm=fake, use_llm=True).run(WASTE_COMPLAINT)

    assert result.category is ComplaintCategory.ROAD_MAINTENANCE
    assert result.department_code == "RIM"
    assert result.needs_human_review is True
    assert result.review_reason is not None


# ---------------------------------------------------------------------------
# Priority safety: the model may escalate on evidence, never de-escalate
# ---------------------------------------------------------------------------

#: Waste complaint that carries corroborating severity evidence ("dangerous",
#: "school") but whose deterministic band is still HIGH, so a model-driven
#: escalation to CRITICAL is a real change of band and not the life-safety floor.
EVIDENCE_BACKED_WASTE_COMPLAINT = (
    "A dead animal carcass has been lying near the school for three days and it is dangerous."
)


def test_model_cannot_downgrade_priority() -> None:
    fake = ScriptedLlm([_payload(priority="low", urgency_score=0.05)])

    result = ClassificationAgent(llm=fake, use_llm=True).run(WASTE_COMPLAINT)

    assert result.priority is ComplaintPriority.HIGH
    assert result.urgency_score >= 0.50
    assert result.deterministic_priority is ComplaintPriority.HIGH
    assert result.escalation_reason is None


def test_model_can_escalate_priority_when_evidence_corroborates() -> None:
    fake = ScriptedLlm([_payload(priority="critical", urgency_score=0.95)])

    result = ClassificationAgent(llm=fake, use_llm=True).run(EVIDENCE_BACKED_WASTE_COMPLAINT)

    assert result.deterministic_priority is ComplaintPriority.HIGH
    assert result.model_priority is ComplaintPriority.CRITICAL
    assert result.priority is ComplaintPriority.CRITICAL
    assert result.urgency_score >= 0.80
    assert result.escalation_reason is not None
    assert "corroborated" in result.escalation_reason
    # A *supported* escalation is applied, so no human review is triggered.
    assert result.needs_human_review is False


def test_model_escalation_to_critical_without_evidence_is_capped_and_reviewed() -> None:
    """A model's own opinion - confidence included - cannot decide CRITICAL."""
    fake = ScriptedLlm([_payload(priority="critical", urgency_score=0.95, confidence=0.99)])

    result = ClassificationAgent(llm=fake, use_llm=True).run(WASTE_COMPLAINT)

    assert result.deterministic_priority is ComplaintPriority.HIGH
    assert result.model_priority is ComplaintPriority.CRITICAL  # recorded, not applied
    assert result.priority is ComplaintPriority.HIGH
    # Urgency is capped with the band so the two can never contradict.
    assert result.urgency_score < URGENCY_CRITICAL_AT
    assert result.escalation_reason is not None
    assert "capped" in result.escalation_reason
    assert result.needs_human_review is True
    assert result.review_reason == result.escalation_reason


def test_model_may_escalate_to_high_without_extra_evidence() -> None:
    """Only CRITICAL is gated; MEDIUM -> HIGH needs no extra corroboration."""
    fake = ScriptedLlm([_payload(category="street_lighting", priority="high", urgency_score=0.6)])

    result = ClassificationAgent(llm=fake, use_llm=True).run(LIGHTING_COMPLAINT)

    assert result.deterministic_priority is ComplaintPriority.MEDIUM
    assert result.priority is ComplaintPriority.HIGH
    assert result.escalation_reason is not None
    assert result.needs_human_review is False


def test_life_threatening_escalation_forces_critical_urgency() -> None:
    # "live wire" is a life-safety term: a street-lighting complaint must not
    # stay merely "high" while an electrocution hazard is on the street.
    text = "A live wire is hanging from the street light pole near the school."
    result = ClassificationAgent(use_llm=False).run(text)

    assert result.category is ComplaintCategory.STREET_LIGHTING
    assert result.urgency_score >= 0.80
    assert result.priority is ComplaintPriority.CRITICAL
    assert result.deterministic_priority is ComplaintPriority.CRITICAL
    assert result.model_priority is None
    assert "pole" in result.matched_signals
    assert "live wire" in result.reasoning
