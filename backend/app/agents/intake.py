"""Agent 1 - Complaint Intake.

Turns free text (English, Tamil or Tamil-transliterated) into validated,
structured JSON. Three layers cooperate:

1. **Deterministic detection** (language, duration, ward, location phrase,
   landmarks) runs first and always succeeds.
2. **One model call** fills in the rest: an English restatement, a problem-type
   label, and any missing-information it noticed.
3. **Grounding guards** discard every model claim that is not traceable to the
   citizen's own words. This is how "never invent locations or complaint
   details" is enforced in code, not merely requested in a prompt.

If the model is unavailable or returns unusable output, the agent returns the
deterministic result with ``degraded=True`` instead of failing - the caller
still receives a schema-valid analysis.
"""

from __future__ import annotations

import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.agents.errors import AgentLlmError, AgentOutputError
from app.agents.extraction import (
    MISSING_DETAIL,
    MISSING_DURATION,
    MISSING_EVIDENCE,
    MISSING_EXACT_LOCATION,
    MISSING_LANDMARK,
    canonical_missing_item,
    extract_duration,
    extract_landmarks,
    extract_location_phrase,
    extract_ward,
    grounding_ratio,
    has_tamil_script,
    is_grounded,
    location_is_specific,
    normalise,
    question_for,
    tokens,
)
from app.agents.language import detect_language
from app.agents.llm_client import LlmCallResult, LlmClient, get_default_llm_client
from app.agents.parsing import coerce_string_list, extract_json_object
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import get_logger
from app.schemas.agent import IntakeResult

logger = get_logger(__name__)

#: Fewer than this many words is treated as a description too thin to act on.
MIN_DESCRIPTION_TOKENS = 6

#: Upper bound on clarification questions returned to a citizen.
MAX_CLARIFICATION_QUESTIONS = 3

SYSTEM_PROMPT = """\
You are the Complaint Intake Agent inside CivicBridge AI, a municipal grievance system.
You read ONE citizen complaint - written in English, Tamil, or Tamil typed in Latin
letters - and return ONE JSON object.

Hard rules:
- Restate the complaint in clear, simple English in "normalized_description".
- NEVER invent a location, landmark, date, duration, name or any other detail that the
  citizen did not state. Missing detail belongs in "missing_information", not in a guess.
- "missing_information" items are short snake_case keys from this list only:
  exact_location, landmark, duration, evidence_photo, description_detail.
- "clarification_questions" holds at most 3 short questions.
- "confidence" is your own confidence in the extraction, between 0.0 and 1.0.
- "reasoning" is one short sentence explaining your extraction.
- Reply with the JSON object ONLY. No markdown fences, no commentary, no extra keys.
"""

RESPONSE_KEYS = (
    "normalized_description, problem_type, duration_days, duration_text, "
    "location_text, landmarks, missing_information, clarification_questions, "
    "confidence, reasoning"
)


class _IntakeDraft(BaseModel):
    """The LLM-facing subset of the intake result.

    Only fields here may come from the model. ``extra="ignore"`` means a model
    that volunteers a ``department`` or ``language`` field cannot corrupt the
    result: those values are simply never read.
    """

    model_config = ConfigDict(extra="ignore")

    normalized_description: str = ""
    problem_type: str | None = None
    duration_days: float | None = None
    duration_text: str | None = None
    location_text: str | None = None
    landmarks: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    reasoning: str = ""

    @field_validator("landmarks", "missing_information", "clarification_questions", mode="before")
    @classmethod
    def _normalise_list_fields(cls, value: Any) -> Any:
        """Repair an object/string where the schema asks for an array.

        ``llama3.1`` returns ``{"exact_location": "..."}`` for
        ``missing_information`` often enough that rejecting it would throw away
        an otherwise good answer. A genuinely unrepairable type still fails
        validation, so the repair-then-fallback path is preserved.
        """
        if value is None or isinstance(value, list):
            return value
        coerced = coerce_string_list(value)
        if coerced is None:
            raise ValueError("expected an array of strings")
        return coerced

    @field_validator("problem_type", mode="before")
    @classmethod
    def _repair_problem_type(cls, value: Any) -> Any:
        """Repair a list where a single label is expected.

        ``llama3.1`` answers this scalar field with ``["streetlight"]`` (or
        ``["Public Health", "Sanitation"]``) often enough that rejecting it
        would throw away an otherwise good response. A list of labels is joined
        into one; a genuinely unrepairable type still fails validation, so the
        repair-then-fallback path is preserved.
        """
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            labels = [item.strip() for item in value if isinstance(item, str) and item.strip()]
            return ", ".join(labels) or None
        return value


def _build_user_prompt(text: str) -> str:
    return (
        "Complaint:\n"
        f'"""\n{text}\n"""\n\n'
        "Return one JSON object with exactly these keys:\n"
        f"{{{RESPONSE_KEYS}}}\n"
        "duration_days is a number of days or null. location_text and duration_text are "
        "strings copied from the complaint or null. landmarks, missing_information and "
        "clarification_questions MUST be JSON arrays of short strings - never an object."
    )


def _build_repair_prompt(text: str, error: str, previous: str) -> str:
    return (
        "Your previous reply could not be used.\n"
        f"Problem: {error}\n\n"
        "Reply again with ONE JSON object using exactly these keys:\n"
        f"{{{RESPONSE_KEYS}}}\n\n"
        "Original complaint:\n"
        f'"""\n{text}\n"""\n'
    ) + (f"\nYour previous reply was:\n{previous[:600]}\n" if previous else "")


def _restatement_is_grounded(restatement: str, source: str, duration_days: float | None) -> bool:
    """Whether an English restatement avoids inventing numbers or places.

    Tamil-to-English restatement cannot be compared token by token, so the check
    focuses on the two things that do the real damage if fabricated: figures and
    locations.
    """
    allowed_numbers: set[str] = set()
    if duration_days is not None:
        allowed_numbers.update({str(int(duration_days)), f"{duration_days:g}"})

    source_norm = normalise(source)
    for digits in re.findall(r"\d+", restatement):
        if digits in allowed_numbers:
            continue
        if digits.lstrip("0") and digits.lstrip("0") in source_norm:
            continue
        return False

    phrase = extract_location_phrase(restatement)
    if not phrase:
        return True
    # A Tamil-to-English restatement cannot be compared token by token: an
    # English place name never matches the Tamil source, so demanding a
    # grounding ratio here would discard every faithful translation and leave
    # the description in Tamil. The numeric check above is script-neutral and
    # still applies, as does the separate grounding check on the location field.
    if has_tamil_script(phrase) != has_tamil_script(source):
        return True
    return grounding_ratio(phrase, source) >= 0.4


class IntakeAgent:
    """Extract structured facts from one complaint, with a safe fallback."""

    def __init__(
        self,
        *,
        llm: LlmClient | None = None,
        settings: Settings | None = None,
        use_llm: bool | None = None,
        max_repair_attempts: int | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._use_llm = self._settings.agent_use_llm if use_llm is None else use_llm
        self._max_repair = (
            self._settings.agent_max_repair_attempts
            if max_repair_attempts is None
            else max_repair_attempts
        )
        self._llm = (
            llm if llm is not None else (get_default_llm_client() if self._use_llm else None)
        )

    # -- public API --------------------------------------------------------

    def run(self, text: str) -> IntakeResult:
        """Analyse ``text`` and return a validated :class:`IntakeResult`."""
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("complaint text must not be empty")

        started = time.perf_counter()
        detection = detect_language(cleaned)

        duration = extract_duration(cleaned)
        ward = extract_ward(cleaned)
        deterministic_location = extract_location_phrase(cleaned)
        deterministic_landmarks = extract_landmarks(cleaned)

        draft: _IntakeDraft | None = None
        call: LlmCallResult | None = None
        degraded = False

        if self._use_llm and self._llm is not None and self._llm.is_available():
            try:
                draft, call = self._call_model(cleaned)
            except (AgentLlmError, AgentOutputError) as exc:
                logger.warning("Intake agent fell back to deterministic output: %s", exc)
                degraded = True
        elif self._use_llm:
            degraded = True

        return self._assemble(
            cleaned=cleaned,
            detection=detection,
            duration=duration,
            ward=ward,
            deterministic_location=deterministic_location,
            deterministic_landmarks=deterministic_landmarks,
            draft=draft,
            call=call,
            degraded=degraded,
            started=started,
        )

    # -- model call --------------------------------------------------------

    def _call_model(self, text: str) -> tuple[_IntakeDraft, LlmCallResult]:
        """One call, plus at most ``max_repair_attempts`` schema-repair calls."""
        assert self._llm is not None  # guarded by the caller
        user = _build_user_prompt(text)
        call = self._llm.complete(system=SYSTEM_PROMPT, user=user)
        attempts = 0

        while True:
            try:
                payload = extract_json_object(call.text)
                return _IntakeDraft.model_validate(payload), call
            except (AgentOutputError, ValidationError) as exc:
                if attempts >= self._max_repair:
                    raise AgentOutputError(
                        f"intake output failed validation after {attempts + 1} attempt(s): {exc}"
                    ) from exc
                attempts += 1
                logger.info("Requesting intake JSON repair (attempt %s): %s", attempts, exc)
                call = self._llm.complete(
                    system=SYSTEM_PROMPT,
                    user=_build_repair_prompt(text, str(exc), call.text),
                )

    # -- assembly ----------------------------------------------------------

    def _assemble(
        self,
        *,
        cleaned: str,
        detection,
        duration,
        ward: str | None,
        deterministic_location: str | None,
        deterministic_landmarks: list[str],
        draft: _IntakeDraft | None,
        call: LlmCallResult | None,
        degraded: bool,
        started: float,
    ) -> IntakeResult:
        dropped: list[str] = []
        missing: list[str] = []

        # -- location ------------------------------------------------------
        location_text = deterministic_location or ward
        if draft and draft.location_text and draft.location_text.strip():
            candidate = draft.location_text.strip()
            if is_grounded(candidate, cleaned):
                # Compare word counts, not characters: the model's "வார்டு 10" is
                # longer in characters than the canonical "Ward 10" but says the
                # same thing, so a character comparison displaces the
                # deterministic form for no gain.
                if len(candidate.split()) > len((location_text or "").split()):
                    location_text = candidate
            else:
                dropped.append(f"location:{candidate}")
        if not location_is_specific(location_text, cleaned):
            missing.append(MISSING_EXACT_LOCATION)

        # -- landmarks -----------------------------------------------------
        landmarks = list(deterministic_landmarks)
        if draft:
            seen = {item.lower() for item in landmarks}
            for landmark in draft.landmarks:
                candidate = (landmark or "").strip()
                if not candidate or candidate.lower() in seen:
                    continue
                if is_grounded(candidate, cleaned):
                    landmarks.append(candidate)
                    seen.add(candidate.lower())
                else:
                    dropped.append(f"landmark:{candidate}")
        landmarks = landmarks[:5]
        if not landmarks and not location_is_specific(location_text, cleaned):
            missing.append(MISSING_LANDMARK)

        # -- duration ------------------------------------------------------
        duration_days = duration.days if duration else None
        duration_text = duration.text if duration else None
        if duration_days is None and draft is not None and draft.duration_days is not None:
            if self._draft_duration_is_grounded(draft, cleaned):
                duration_days = float(draft.duration_days)
                duration_text = draft.duration_text
            else:
                dropped.append(f"duration:{draft.duration_days}")
        if duration_days is None:
            missing.append(MISSING_DURATION)

        # -- description quality -------------------------------------------
        token_count = len(tokens(cleaned))
        if token_count < MIN_DESCRIPTION_TOKENS:
            missing.append(MISSING_DETAIL)

        # -- normalised description ----------------------------------------
        normalized = cleaned
        if draft and draft.normalized_description.strip():
            candidate = draft.normalized_description.strip()
            if _restatement_is_grounded(candidate, cleaned, duration_days):
                normalized = candidate
            else:
                dropped.append("normalized_description:ungrounded_detail")

        # -- missing information + questions --------------------------------
        for item in draft.missing_information if draft else []:
            canonical = canonical_missing_item(item)
            if canonical not in missing:
                missing.append(canonical)

        questions: list[str] = []
        if draft:
            for question in draft.clarification_questions:
                text = (question or "").strip()
                if 0 < len(text) <= 200 and text not in questions:
                    questions.append(text)
        for item in missing:
            question = question_for(item)
            if question not in questions:
                questions.append(question)
        needs_clarification = bool(missing)
        if needs_clarification and not any("photo" in q.lower() for q in questions):
            evidence_question = question_for(MISSING_EVIDENCE)
            if len(questions) < MAX_CLARIFICATION_QUESTIONS:
                questions.append(evidence_question)
        questions = questions[:MAX_CLARIFICATION_QUESTIONS]

        # -- confidence ----------------------------------------------------
        if draft is not None:
            confidence = max(0.0, min(1.0, float(draft.confidence)))
        else:
            # Honest ceiling: without a model the deterministic layer only knows
            # the features it can measure.
            signals = sum(
                1
                for flag in (
                    location_is_specific(location_text, cleaned),
                    duration_days is not None,
                    bool(landmarks),
                )
                if flag
            )
            confidence = min(0.55, 0.2 + 0.12 * signals)

        reasoning = (
            draft.reasoning.strip()
            if draft and draft.reasoning.strip()
            else (
                f"Deterministic extraction only (language={detection.language.value}, "
                f"location_specific={location_is_specific(location_text, cleaned)}, "
                f"duration_found={duration_days is not None})."
            )
        )
        if dropped:
            reasoning += f" Discarded unsupported model details: {', '.join(dropped)}."

        return IntakeResult(
            original_text=cleaned,
            language=detection.language,
            language_confidence=detection.confidence,
            language_reason=detection.reason,
            normalized_description=normalized,
            problem_type=(draft.problem_type.strip() if draft and draft.problem_type else None),
            duration_days=duration_days,
            duration_text=duration_text,
            location_text=location_text,
            ward=ward,
            landmarks=landmarks,
            missing_information=missing,
            clarification_questions=questions,
            needs_clarification=needs_clarification,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            unsupported_details_dropped=dropped,
            llm_used=draft is not None,
            degraded=degraded,
            latency_ms=call.latency_ms if call else None,
            model_name=call.model if call else None,
        )

    @staticmethod
    def _draft_duration_is_grounded(draft: _IntakeDraft, source: str) -> bool:
        """A model-supplied duration is only trusted when the citizen stated it."""
        if draft.duration_text and is_grounded(draft.duration_text, source, minimum=0.5):
            return True
        if draft.duration_days is None:
            return False
        if MISSING_DURATION in draft.missing_information:
            return False
        source_norm = normalise(source)
        return any(
            value in source_norm
            for value in {str(int(draft.duration_days)), f"{draft.duration_days:g}"}
        )


__all__ = [
    "MAX_CLARIFICATION_QUESTIONS",
    "MIN_DESCRIPTION_TOKENS",
    "RESPONSE_KEYS",
    "SYSTEM_PROMPT",
    "IntakeAgent",
]
