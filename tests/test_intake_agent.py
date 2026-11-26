"""Phase 3 unit tests for the Complaint Intake Agent.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_intake_agent.py -v

Every test in this module drives the agent with a **scripted fake LLM client**,
so the assertions are deterministic and no inference happens. Tests that use
the real ``llama3.1:latest`` model live in ``test_phase3_integration.py`` and
skip automatically when Ollama is unavailable.
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

from app.agents.errors import AgentLlmError
from app.agents.intake import IntakeAgent
from app.agents.llm_client import LlmCallResult
from app.models.enums import ComplaintLanguage
from app.schemas.agent import IntakeResult

ENGLISH_COMPLAINT = "For the past five days garbage has not been collected near our street."
TAMIL_COMPLAINT = "வார்டு 10 இல் 5 நாட்கள் குப்பை அள்ளப்படவில்லை"
TRANSLITERATED_COMPLAINT = "kuppai theru la 5 naatkal collect pannala"
INCOMPLETE_COMPLAINT = "Garbage problem"
AMBIGUOUS_COMPLAINT = "Something is wrong in our area please fix it soon"


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
    """A schema-valid intake draft, with targeted fields overridden."""
    draft: dict[str, object] = {
        "normalized_description": "Garbage has not been collected for five days.",
        "problem_type": "garbage_not_collected",
        "duration_days": 5,
        "duration_text": "five days",
        "location_text": None,
        "landmarks": [],
        "missing_information": ["exact_location"],
        "clarification_questions": ["Which street is affected?"],
        "confidence": 0.8,
        "reasoning": "The citizen reports five days of missed collection.",
    }
    draft.update(overrides)
    return json.dumps(draft)


# ---------------------------------------------------------------------------
# 1-3. Language handling (English, Tamil script, Tamil transliteration)
# ---------------------------------------------------------------------------


def test_english_complaint_is_detected_with_duration() -> None:
    result = IntakeAgent(use_llm=False).run(ENGLISH_COMPLAINT)

    assert isinstance(result, IntakeResult)
    assert result.language is ComplaintLanguage.EN
    assert result.duration_days == 5.0
    assert result.original_text == ENGLISH_COMPLAINT
    assert result.llm_used is False


def test_tamil_script_complaint_is_detected() -> None:
    result = IntakeAgent(use_llm=False).run(TAMIL_COMPLAINT)

    assert result.language is ComplaintLanguage.TA
    assert result.ward == "Ward 10"
    assert result.duration_days == 5.0
    # A Tamil complaint naming a ward and a duration is complete enough to act on.
    assert result.needs_clarification is False


def test_tamil_transliterated_complaint_is_detected() -> None:
    result = IntakeAgent(use_llm=False).run(TRANSLITERATED_COMPLAINT)

    assert result.language is ComplaintLanguage.TA_LATN
    assert result.duration_days == 5.0
    assert result.needs_clarification is True
    assert "exact_location" in result.missing_information


# ---------------------------------------------------------------------------
# 4-5. Incomplete and ambiguous complaints
# ---------------------------------------------------------------------------


def test_incomplete_complaint_asks_for_clarification() -> None:
    result = IntakeAgent(use_llm=False).run(INCOMPLETE_COMPLAINT)

    assert result.needs_clarification is True
    assert "description_detail" in result.missing_information
    assert "exact_location" in result.missing_information
    assert result.clarification_questions
    assert len(result.clarification_questions) <= 3


def test_ambiguous_complaint_is_flagged_with_low_confidence() -> None:
    result = IntakeAgent(use_llm=False).run(AMBIGUOUS_COMPLAINT)

    assert result.needs_clarification is True
    assert result.duration_days is None
    assert result.confidence <= 0.55
    assert result.clarification_questions


def test_empty_complaint_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        IntakeAgent(use_llm=False).run("   ")


# ---------------------------------------------------------------------------
# 6-8. Grounding: the model may not invent locations, landmarks or durations
# ---------------------------------------------------------------------------


def test_model_invented_location_and_landmark_are_discarded() -> None:
    fake = ScriptedLlm(
        [
            _payload(
                normalized_description="Garbage has not been collected for five days.",
                location_text="12 Gandhi Street, Chennai",
                landmarks=["Famous Temple"],
            )
        ]
    )

    result = IntakeAgent(llm=fake, use_llm=True).run(ENGLISH_COMPLAINT)

    assert result.llm_used is True
    # The model's fabricated address and landmark are dropped; the only location
    # evidence that survives is the citizen's own "near our street" wording.
    assert result.location_text == "our street"
    assert "Famous Temple" not in result.landmarks
    assert any(item.startswith("location:") for item in result.unsupported_details_dropped)
    assert any(item.startswith("landmark:") for item in result.unsupported_details_dropped)


def test_grounded_but_non_address_location_is_discarded() -> None:
    """A grounded phrase that is not an address must not become the location.

    "my house" is traceable to the citizen's wording, so grounding alone accepts
    it, but it names no place a field crew could be sent to. The model candidate
    must still be dropped and the location left null.
    """
    source = "Street light not working near my house"
    fake = ScriptedLlm(
        [
            _payload(
                normalized_description="The street light near my house is not working.",
                location_text="my house",
            )
        ]
    )

    result = IntakeAgent(llm=fake, use_llm=True).run(source)

    assert result.llm_used is True
    assert result.location_text is None
    assert "location:my house" in result.unsupported_details_dropped


def test_model_invented_duration_is_discarded() -> None:
    source = "There is a problem outside my house."
    fake = ScriptedLlm(
        [
            _payload(
                normalized_description="There is a problem outside my house.",
                duration_days=3,
                duration_text="three days",
                missing_information=["duration", "exact_location"],
            )
        ]
    )

    result = IntakeAgent(llm=fake, use_llm=True).run(source)

    assert result.duration_days is None
    assert any(item.startswith("duration:") for item in result.unsupported_details_dropped)


def test_model_cannot_override_deterministic_language() -> None:
    fake = ScriptedLlm([_payload(language="ta", department="SWM")])

    result = IntakeAgent(llm=fake, use_llm=True).run(ENGLISH_COMPLAINT)

    # ``extra="ignore"`` on the draft model means both fields are simply unread.
    assert result.language is ComplaintLanguage.EN


# ---------------------------------------------------------------------------
# 9-10. Invalid model output and an unavailable model
# ---------------------------------------------------------------------------


def test_invalid_model_output_repairs_then_falls_back() -> None:
    fake = ScriptedLlm(["this is not json", "still not json"])

    result = IntakeAgent(llm=fake, use_llm=True).run(ENGLISH_COMPLAINT)

    assert result.degraded is True
    assert result.llm_used is False
    assert result.duration_days == 5.0
    assert len(fake.calls) == 2  # one attempt plus one repair attempt


def test_unavailable_model_degrades_without_calling_it() -> None:
    fake = ScriptedLlm([], available=False)

    result = IntakeAgent(llm=fake, use_llm=True).run(ENGLISH_COMPLAINT)

    assert result.degraded is True
    assert result.llm_used is False
    assert fake.calls == []
    assert result.language is ComplaintLanguage.EN


def test_fenced_json_is_recovered() -> None:
    fenced = f"```json\n{_payload()}\n```"
    fake = ScriptedLlm([fenced])

    result = IntakeAgent(llm=fake, use_llm=True).run(ENGLISH_COMPLAINT)

    assert result.llm_used is True
    assert result.degraded is False
    assert result.normalized_description == "Garbage has not been collected for five days."


def test_object_shaped_list_fields_are_repaired_without_a_repair_call() -> None:
    """``llama3.1`` answers a requested array with an object; recover it in place."""
    draft = json.loads(_payload())
    draft["missing_information"] = {"exact_location": "near our street", "description_detail": None}
    draft["landmarks"] = {"near our street": True}
    draft["clarification_questions"] = {"1": "Which street is affected?"}
    fake = ScriptedLlm([json.dumps(draft)])

    result = IntakeAgent(llm=fake, use_llm=True).run(ENGLISH_COMPLAINT)

    assert len(fake.calls) == 1  # the shape was repaired, so no second inference
    assert result.llm_used is True
    assert result.degraded is False
    assert result.normalized_description == draft["normalized_description"]
    assert {"exact_location", "description_detail"} <= set(result.missing_information)
    assert "near our street" in result.landmarks


def test_unrepairable_list_field_still_requests_a_repair() -> None:
    """A wrong *type* (not merely a wrong shape) keeps the documented retry."""
    draft = json.loads(_payload())
    draft["missing_information"] = 42
    fake = ScriptedLlm([json.dumps(draft), _payload()])

    result = IntakeAgent(llm=fake, use_llm=True).run(ENGLISH_COMPLAINT)

    assert len(fake.calls) == 2  # one attempt plus one repair attempt
    assert result.llm_used is True
    assert result.degraded is False
