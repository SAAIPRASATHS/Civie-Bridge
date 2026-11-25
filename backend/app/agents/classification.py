"""Agent 2 - Complaint Classification.

Assigns one of the four supported categories, a subcategory, a priority, an
urgency score and a recommended next action - and, separately, the department
that owns the complaint.

Three rules shape the design:

* **The model never assigns a department.** ``department_code``,
  ``department_name``, ``guideline_code`` and ``sla_hours`` are read from
  :data:`app.datasets.reference_data.DEPARTMENT_DIRECTORY` for the chosen
  category. The model-facing draft model has no department field at all, so a
  model that volunteers one cannot corrupt the routing decision.
* **Deterministic keyword evidence is always computed**, even when the model is
  available. It is the fallback when the model is down, the cross-check when
  the model disagrees, and the source of the ``matched_signals`` explanation.
* **Low confidence is escalated, never hidden.** Below
  ``settings.agent_confidence_threshold`` (and on any model/deterministic
  disagreement) the result is flagged ``needs_human_review=True``.
* **Model escalation is bounded by evidence.** The model may raise the priority
  - never lower it - but ``CRITICAL`` needs corroborating severity evidence in
  the complaint itself. Without it the escalation is capped at
  :data:`MODEL_ESCALATION_CEILING` and routed to a human, because a model's own
  confidence score must never be able to decide a critical priority. The
  deterministic band, the model's recommendation, the applied band and the
  reason for any difference are all recorded on the result.

At most one model call is made per complaint, plus at most
``settings.agent_max_repair_attempts`` schema-repair calls. Reference data is
indexed once at import time.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.errors import AgentLlmError, AgentOutputError
from app.agents.extraction import extract_duration
from app.agents.llm_client import LlmCallResult, LlmClient, get_default_llm_client
from app.agents.parsing import extract_json_object
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import get_logger
from app.datasets.reference_data import DEPARTMENT_DIRECTORY, guideline_by_department_code
from app.models.enums import ComplaintCategory, ComplaintLanguage, ComplaintPriority
from app.schemas.agent import ClassificationResult, IntakeResult

logger = get_logger(__name__)

#: Upper bound on the keyword evidence reported back to the caller.
MAX_MATCHED_SIGNALS = 8

#: Urgency -> priority bands. Kept here so the mapping is inspectable.
URGENCY_CRITICAL_AT = 0.80
URGENCY_HIGH_AT = 0.50
URGENCY_MEDIUM_AT = 0.30

SYSTEM_PROMPT = """\
You are the Classification Agent inside CivicBridge AI, a municipal grievance system.
You read ONE citizen complaint and return ONE JSON object.

Allowed category values (use exactly these strings):
  waste_management, road_maintenance, water_supply, street_lighting
Allowed priority values (use exactly these strings):
  low, medium, high, critical

Rules:
- "category" MUST be one of the allowed category strings.
- "priority" MUST be one of the allowed priority strings.
- Do NOT name or guess a department. Department routing is assigned by the system.
- "urgency_score" is a number from 0.0 to 1.0.
- "priority" is your recommendation. The system applies "critical" only when the
  complaint itself describes a critical condition, so do not choose "critical"
  merely because the problem is important.
- "confidence" is your own confidence in this classification, from 0.0 to 1.0.
- "reasoning" is at most two short sentences and must quote words from the complaint.
- "recommended_next_action" is one short, concrete instruction for a municipal officer.
- Reply with the JSON object ONLY. No markdown fences, no commentary, no extra keys.
"""

RESPONSE_KEYS = (
    "category, subcategory, priority, urgency_score, confidence, reasoning, recommended_next_action"
)

# ---------------------------------------------------------------------------
# Deterministic keyword evidence
# ---------------------------------------------------------------------------

#: English, Tamil script and Tamil transliteration terms per category. Tamil
#: script terms are matched as substrings (agglutination); ASCII terms are
#: matched from a word boundary so "road" cannot fire inside "broad".
CATEGORY_KEYWORDS: dict[ComplaintCategory, tuple[str, ...]] = {
    ComplaintCategory.WASTE_MANAGEMENT: (
        "garbage",
        "waste",
        "trash",
        "rubbish",
        "litter",
        "dumping",
        "dump",
        "dustbin",
        "bin",
        "sweeping",
        "sanitation",
        "black spot",
        "stench",
        "filth",
        "carcass",
        "dead animal",
        "drain desilting",
        "toilet",
        "compost",
        "குப்பை",
        "கழிவு",
        "தொட்டி",
        "நாற்றம்",
        "தூய்மை",
        "சுத்தம்",
        "வீண்",
        "kuppai",
        "kazhivu",
        "thotti",
        "naatram",
        "setham",
        "thooimai",
        "sutham",
    ),
    ComplaintCategory.ROAD_MAINTENANCE: (
        "pothole",
        "road",
        "pavement",
        "footpath",
        "sidewalk",
        "asphalt",
        "tar",
        "resurfacing",
        "crack",
        "speed breaker",
        "speed bump",
        "uneven",
        "kerb",
        "curb",
        "bridge",
        "flyover",
        "சாலை",
        "பள்ளம்",
        "நடைபாதை",
        "தார்",
        "உடைந்த",
        "saalai",
        "salaip",
        "nadaipaathai",
        "paarai",
    ),
    ComplaintCategory.WATER_SUPPLY: (
        "water",
        "drinking water",
        "tap",
        "pipeline",
        "pipe",
        "leak",
        "supply",
        "contaminated",
        "contamination",
        "muddy",
        "sewage",
        "sewer",
        "tanker",
        "borewell",
        "sump",
        "overhead tank",
        "water tank",
        "low pressure",
        "தண்ணீர்",
        "தண்ணி",
        "குழாய்",
        "நீர்",
        "சாக்கடை",
        "கழிவுநீர்",
        "குடிநீர்",
        "அழுக்கு",
        "thanneer",
        "thanni",
        "kuzhaay",
        "kuzhai",
        "kasivu",
        "neer",
        "saakkadai",
        "kudineer",
        "azhukku",
    ),
    ComplaintCategory.STREET_LIGHTING: (
        "street light",
        "streetlight",
        "street lights",
        "lamp post",
        "lamppost",
        "lamp",
        "pole",
        "bulb",
        "flickering",
        "unlit",
        "dark street",
        "darkness",
        "illumination",
        "cabling",
        "wiring",
        "electric",
        "விளக்கு",
        "மின்சாரம்",
        "இருள்",
        "கம்பம்",
        "ஒளி",
        "vilakku",
        "vilakkil",
        "minsaram",
        "irul",
        "kambam",
    ),
}

#: Severity terms that raise urgency regardless of category.
ESCALATION_KEYWORDS: tuple[str, ...] = (
    "accident",
    "injured",
    "injury",
    "died",
    "death",
    "electrocution",
    "electric shock",
    "live wire",
    "open manhole",
    "collapsed",
    "collapse",
    "burst",
    "flooded",
    "flooding",
    "drowning",
    "contaminated",
    "dangerous",
    "emergency",
    "urgent",
    "hospital",
    "school",
    "children",
    "pregnant",
    "elderly",
    "dengue",
    "malaria",
    "epidemic",
    "outbreak",
    "snake",
    "fire",
    "விபத்து",
    "காயம்",
    "ஆபத்து",
    "நோய்",
    "vibathu",
    "kaayam",
    "aabathu",
    "avasaram",
)

#: Escalation terms that describe an immediate threat to life. One of these
#: forces critical urgency whatever the category, because an under-triaged
#: electrocution, drowning or collapse risk is the worst failure this agent can
#: make. Vulnerability markers ("school", "elderly") are deliberately excluded:
#: they raise concern, not the priority band.
CRITICAL_ESCALATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "accident",
        "injured",
        "injury",
        "died",
        "death",
        "electrocution",
        "electric shock",
        "live wire",
        "open manhole",
        "drowning",
        "collapsed",
        "collapse",
        "fire",
        "epidemic",
        "outbreak",
        "snake",
        "விபத்து",
        "காயம்",
        "vibathu",
        "kaayam",
    }
)

#: Subcategory keyword tables, evaluated in order; first match wins.
SUBCATEGORY_KEYWORDS: tuple[tuple[ComplaintCategory, str, tuple[str, ...]], ...] = (
    (
        ComplaintCategory.WASTE_MANAGEMENT,
        "garbage_not_collected",
        ("not collected", "not been collected", "collection", "garbage", "kuppai", "குப்பை"),
    ),
    (
        ComplaintCategory.WASTE_MANAGEMENT,
        "bin_overflow",
        ("overflowing bin", "bin overflow", "dustbin", "bin", "தொட்டி", "thotti"),
    ),
    (
        ComplaintCategory.WASTE_MANAGEMENT,
        "drain_desilting",
        ("drain", "desilting", "blocked drain", "சாக்கடை", "setham"),
    ),
    (
        ComplaintCategory.ROAD_MAINTENANCE,
        "pothole",
        ("pothole", "potholes", "பள்ளம்", "pallam"),
    ),
    (
        ComplaintCategory.ROAD_MAINTENANCE,
        "footpath_damage",
        ("footpath", "sidewalk", "pavement", "நடைபாதை", "nadaipaathai"),
    ),
    (
        ComplaintCategory.ROAD_MAINTENANCE,
        "road_damage",
        ("road", "resurfacing", "crack", "uneven", "சாலை", "saalai"),
    ),
    (
        ComplaintCategory.WATER_SUPPLY,
        "contaminated_water",
        ("contaminated", "muddy", "dirty water", "அழுக்கு", "azhukku"),
    ),
    (
        ComplaintCategory.WATER_SUPPLY,
        "pipeline_leak",
        ("leak", "leakage", "burst pipe", "குழாய்", "kuzhaay"),
    ),
    (
        ComplaintCategory.WATER_SUPPLY,
        "sewage_overflow",
        ("sewage", "sewer", "கழிவுநீர்", "kasivu"),
    ),
    (
        ComplaintCategory.WATER_SUPPLY,
        "no_water_supply",
        ("no water", "water supply", "low pressure", "தண்ணீர்", "thanneer", "thanni"),
    ),
    (
        ComplaintCategory.STREET_LIGHTING,
        "light_not_working",
        ("not working", "flickering", "unlit", "விளக்கு", "vilakku"),
    ),
    (
        ComplaintCategory.STREET_LIGHTING,
        "pole_damage",
        ("pole", "cabling", "wiring", "கம்பம்", "kambam"),
    ),
)

#: Baseline urgency per category, aligned with the prototype service guidelines
#: (water is mission-critical, street lighting least so).
BASE_URGENCY: dict[ComplaintCategory, float] = {
    ComplaintCategory.WATER_SUPPLY: 0.62,
    ComplaintCategory.ROAD_MAINTENANCE: 0.50,
    ComplaintCategory.WASTE_MANAGEMENT: 0.48,
    ComplaintCategory.STREET_LIGHTING: 0.42,
}

DEFAULT_ACTIONS: dict[ComplaintCategory, str] = {
    ComplaintCategory.WASTE_MANAGEMENT: (
        "Dispatch the collection beat to clear the reported spot and confirm with a photo."
    ),
    ComplaintCategory.ROAD_MAINTENANCE: (
        "Schedule a field inspection of the reported stretch and barricade any safety hazard."
    ),
    ComplaintCategory.WATER_SUPPLY: (
        "Verify the affected pipeline segment, restore supply and arrange a tanker if the "
        "repair will exceed the SLA."
    ),
    ComplaintCategory.STREET_LIGHTING: (
        "Assign an electrical crew to the reported pole and test the fitting after dusk."
    ),
}

UNDETERMINED_ACTION = (
    "Route to a human officer for manual triage: the category could not be determined "
    "from the complaint text."
)

_PRIORITY_RANK: dict[ComplaintPriority, int] = {
    ComplaintPriority.LOW: 0,
    ComplaintPriority.MEDIUM: 1,
    ComplaintPriority.HIGH: 2,
    ComplaintPriority.CRITICAL: 3,
}

#: Lowest urgency consistent with each priority band, so the two never diverge.
_PRIORITY_FLOOR: dict[ComplaintPriority, float] = {
    ComplaintPriority.LOW: 0.0,
    ComplaintPriority.MEDIUM: URGENCY_MEDIUM_AT,
    ComplaintPriority.HIGH: URGENCY_HIGH_AT,
    ComplaintPriority.CRITICAL: URGENCY_CRITICAL_AT,
}

#: The highest band a model may reach **on its own**. A model-driven escalation
#: to ``CRITICAL`` needs corroborating severity evidence from the citizen's own
#: words (see :data:`ESCALATION_KEYWORDS`); without it the escalation is capped
#: here and the complaint is sent to a human. A model's confidence score - like
#: any other model opinion - can never on its own decide a critical priority.
MODEL_ESCALATION_CEILING = ComplaintPriority.HIGH

#: Highest urgency consistent with each band. Applied when an escalation is
#: refused, so the reported urgency can never contradict the applied band.
_URGENCY_BAND_CEILING: dict[ComplaintPriority, float] = {
    ComplaintPriority.LOW: URGENCY_MEDIUM_AT - 0.01,
    ComplaintPriority.MEDIUM: URGENCY_HIGH_AT - 0.01,
    ComplaintPriority.HIGH: URGENCY_CRITICAL_AT - 0.01,
    ComplaintPriority.CRITICAL: 1.0,
}

#: Reference data indexed once - these lookups run on every single request.
_DEPARTMENT_BY_CATEGORY: dict[ComplaintCategory, dict] = {
    department["category"]: department for department in DEPARTMENT_DIRECTORY
}
_GUIDELINE_BY_DEPARTMENT_CODE: dict[str, dict] = guideline_by_department_code()


def _compile(keyword: str) -> re.Pattern[str]:
    """Word-start match for ASCII terms, substring match for Tamil script."""
    if keyword.isascii():
        joined = r"\s+".join(re.escape(part) for part in keyword.split())
        if keyword.isalpha():
            return re.compile(rf"\b{joined}", re.IGNORECASE)
        return re.compile(joined, re.IGNORECASE)
    return re.compile(re.escape(keyword), re.IGNORECASE)


_CATEGORY_PATTERNS: dict[ComplaintCategory, tuple[tuple[str, re.Pattern[str]], ...]] = {
    category: tuple((keyword, _compile(keyword)) for keyword in keywords)
    for category, keywords in CATEGORY_KEYWORDS.items()
}
_ESCALATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (keyword, _compile(keyword)) for keyword in ESCALATION_KEYWORDS
)
_SUBCATEGORY_PATTERNS: tuple[tuple[ComplaintCategory, str, tuple[re.Pattern[str], ...]], ...] = (
    tuple(
        (category, label, tuple(_compile(keyword) for keyword in keywords))
        for category, label, keywords in SUBCATEGORY_KEYWORDS
    )
)


# ---------------------------------------------------------------------------
# Deterministic read
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ClassificationInput:
    """Normalised input, independent of whether an IntakeResult was supplied."""

    text: str
    problem_type: str | None
    duration_days: float | None
    needs_clarification: bool
    language: ComplaintLanguage | None


@dataclass(frozen=True, slots=True)
class DeterministicRead:
    """What the keyword evidence alone concludes."""

    category: ComplaintCategory | None
    subcategory: str | None
    priority: ComplaintPriority
    urgency_score: float
    confidence: float
    matched_signals: tuple[str, ...]
    escalations: tuple[str, ...]
    runner_up: ComplaintCategory | None


def priority_for_urgency(urgency: float) -> ComplaintPriority:
    if urgency >= URGENCY_CRITICAL_AT:
        return ComplaintPriority.CRITICAL
    if urgency >= URGENCY_HIGH_AT:
        return ComplaintPriority.HIGH
    if urgency >= URGENCY_MEDIUM_AT:
        return ComplaintPriority.MEDIUM
    return ComplaintPriority.LOW


def _matched_signals(text: str) -> dict[ComplaintCategory, list[str]]:
    return {
        category: [keyword for keyword, pattern in patterns if pattern.search(text)]
        for category, patterns in _CATEGORY_PATTERNS.items()
    }


def _subcategory_for(text: str, category: ComplaintCategory) -> str | None:
    for candidate_category, label, patterns in _SUBCATEGORY_PATTERNS:
        if candidate_category is not category:
            continue
        if any(pattern.search(text) for pattern in patterns):
            return label
    return None


def deterministic_read(payload: _ClassificationInput) -> DeterministicRead:
    """Score the complaint text with keywords only. Never raises, never calls a model."""
    haystack = payload.text
    if payload.problem_type:
        # "garbage_not_collected" is strong evidence; treat it as searchable text.
        haystack = f"{haystack} {payload.problem_type.replace('_', ' ')}"

    hits = _matched_signals(haystack)
    ranked = sorted(hits.items(), key=lambda item: len(item[1]), reverse=True)
    best_category, best_hits = ranked[0]
    runner_up_hits = len(ranked[1][1]) if len(ranked) > 1 else 0
    runner_up = ranked[1][0] if len(ranked) > 1 and ranked[1][1] else None

    escalations = tuple(
        keyword for keyword, pattern in _ESCALATION_PATTERNS if pattern.search(haystack)
    )
    life_threatening = any(keyword in CRITICAL_ESCALATION_KEYWORDS for keyword in escalations)

    if not best_hits:
        urgency = min(1.0, 0.3 + min(0.3, 0.1 * len(escalations)))
        if life_threatening:
            urgency = max(urgency, URGENCY_CRITICAL_AT)
        return DeterministicRead(
            category=None,
            subcategory=None,
            priority=priority_for_urgency(urgency),
            urgency_score=round(urgency, 3),
            confidence=0.0,
            matched_signals=(),
            escalations=escalations,
            runner_up=None,
        )

    top = len(best_hits)
    separation = (top - runner_up_hits) / top
    confidence = min(0.9, 0.35 + 0.12 * min(top, 4) + 0.25 * separation)

    duration_bump = min(0.2, (payload.duration_days or 0.0) / 40 * 0.2)
    urgency = BASE_URGENCY[best_category] + min(0.3, 0.1 * len(escalations)) + duration_bump
    if life_threatening:
        urgency = max(urgency, URGENCY_CRITICAL_AT)
    urgency = min(1.0, urgency)

    return DeterministicRead(
        category=best_category,
        subcategory=_subcategory_for(haystack, best_category),
        priority=priority_for_urgency(urgency),
        urgency_score=round(urgency, 3),
        confidence=round(confidence, 3),
        matched_signals=tuple(best_hits[:MAX_MATCHED_SIGNALS]),
        escalations=escalations,
        runner_up=runner_up,
    )


# ---------------------------------------------------------------------------
# Model-facing draft
# ---------------------------------------------------------------------------


class _ClassificationDraft(BaseModel):
    """The only fields the model may contribute.

    There is deliberately no department field: ``extra="ignore"`` plus the
    absence of the field means a model that tries to assign a department is
    simply not read.
    """

    model_config = ConfigDict(extra="ignore")

    category: str | None = None
    subcategory: str | None = None
    priority: str | None = None
    urgency_score: float | None = None
    confidence: float = 0.5
    reasoning: str = ""
    recommended_next_action: str = ""


def _coerce_category(raw: str | None) -> ComplaintCategory | None:
    if not raw:
        return None
    try:
        return ComplaintCategory(str(raw).strip().lower())
    except ValueError:
        return None


def _coerce_priority(raw: str | None) -> ComplaintPriority | None:
    if not raw:
        return None
    try:
        return ComplaintPriority(str(raw).strip().lower())
    except ValueError:
        return None


def _build_user_prompt(payload: _ClassificationInput) -> str:
    context: list[str] = []
    if payload.problem_type:
        context.append(f"Intake problem_type: {payload.problem_type}")
    if payload.duration_days is not None:
        context.append(f"Stated duration: {payload.duration_days:g} day(s)")
    if payload.language is not None:
        context.append(f"Detected language: {payload.language.value}")
    context_text = ("\n".join(context) + "\n\n") if context else ""

    return (
        "Complaint:\n"
        f'"""\n{payload.text}\n"""\n\n'
        f"{context_text}"
        "Return one JSON object with exactly these keys:\n"
        f"{{{RESPONSE_KEYS}}}\n"
        "category must be exactly one of: waste_management, road_maintenance, "
        "water_supply, street_lighting.\n"
        "priority must be exactly one of: low, medium, high, critical.\n"
        "urgency_score and confidence are numbers between 0.0 and 1.0."
    )


def _build_repair_prompt(payload: _ClassificationInput, error: str, previous: str) -> str:
    prompt = (
        "Your previous reply could not be used.\n"
        f"Problem: {error}\n\n"
        "Reply again with ONE JSON object using exactly these keys:\n"
        f"{{{RESPONSE_KEYS}}}\n\n"
        "Original complaint:\n"
        f'"""\n{payload.text}\n"""\n'
    )
    return prompt + (f"\nYour previous reply was:\n{previous[:600]}\n" if previous else "")


def _as_input(source: IntakeResult | str) -> _ClassificationInput:
    if isinstance(source, IntakeResult):
        text = f"{source.original_text}\n{source.normalized_description}".strip()
        return _ClassificationInput(
            text=text or source.original_text,
            problem_type=source.problem_type,
            duration_days=source.duration_days,
            needs_clarification=source.needs_clarification,
            language=source.language,
        )
    text = (source or "").strip()
    # Raw text (e.g. the ``/classify`` endpoint) has no intake result to lean on,
    # so recover any stated duration deterministically rather than assuming none.
    duration = extract_duration(text)
    return _ClassificationInput(
        text=text,
        problem_type=None,
        duration_days=duration.days if duration else None,
        needs_clarification=False,
        language=None,
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ClassificationAgent:
    """Classify one complaint, routing its department from the prototype directory."""

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

    def run(self, source: IntakeResult | str) -> ClassificationResult:
        """Classify ``source`` (an :class:`IntakeResult` or raw complaint text)."""
        payload = _as_input(source)
        if not payload.text:
            raise ValueError("complaint text must not be empty")

        started = time.perf_counter()
        read = deterministic_read(payload)

        draft: _ClassificationDraft | None = None
        call: LlmCallResult | None = None
        degraded = False

        if self._use_llm and self._llm is not None and self._llm.is_available():
            try:
                draft, call = self._call_model(payload)
            except (AgentLlmError, AgentOutputError) as exc:
                logger.warning("Classification agent fell back to keywords: %s", exc)
                degraded = True
        elif self._use_llm:
            degraded = True

        return self._assemble(payload, read, draft, call, degraded, started)

    # -- model call --------------------------------------------------------

    def _call_model(
        self, payload: _ClassificationInput
    ) -> tuple[_ClassificationDraft, LlmCallResult]:
        assert self._llm is not None  # guarded by the caller
        call = self._llm.complete(system=SYSTEM_PROMPT, user=_build_user_prompt(payload))
        attempts = 0

        while True:
            try:
                draft = _ClassificationDraft.model_validate(extract_json_object(call.text))
            except (AgentOutputError, ValidationError) as exc:
                if attempts >= self._max_repair:
                    raise AgentOutputError(
                        f"classification output failed validation after "
                        f"{attempts + 1} attempt(s): {exc}"
                    ) from exc
                attempts += 1
                logger.info("Requesting classification JSON repair (attempt %s): %s", attempts, exc)
                call = self._llm.complete(
                    system=SYSTEM_PROMPT,
                    user=_build_repair_prompt(payload, str(exc), call.text),
                )
                continue

            if _coerce_category(draft.category) is None:
                # An unusable category is a schema failure, not a valid answer:
                # repair once, then fall back rather than forcing a guess.
                if attempts >= self._max_repair:
                    raise AgentOutputError(
                        f"model returned an unsupported category {draft.category!r} after "
                        f"{attempts + 1} attempt(s)"
                    )
                attempts += 1
                logger.info("Requesting classification category repair (attempt %s)", attempts)
                call = self._llm.complete(
                    system=SYSTEM_PROMPT,
                    user=_build_repair_prompt(
                        payload, f"unsupported category {draft.category!r}", call.text
                    ),
                )
                continue

            return draft, call

    # -- assembly ----------------------------------------------------------

    def _assemble(
        self,
        payload: _ClassificationInput,
        read: DeterministicRead,
        draft: _ClassificationDraft | None,
        call: LlmCallResult | None,
        degraded: bool,
        started: float,
    ) -> ClassificationResult:
        notes: list[str] = []
        threshold = self._settings.agent_confidence_threshold
        review_reason: str | None = None
        disagreement = False

        model_category = _coerce_category(draft.category) if draft else None
        model_confidence = max(0.0, min(1.0, float(draft.confidence))) if draft is not None else 0.0

        # -- category ------------------------------------------------------
        if read.category is not None and model_category is not None:
            if read.category is model_category:
                category = read.category
                confidence = min(0.97, max(read.confidence, model_confidence) + 0.08)
                notes.append("model agreed with the deterministic evidence")
            else:
                disagreement = True
                review_reason = (
                    f"deterministic evidence says {read.category.value} but the model says "
                    f"{model_category.value}"
                )
                if model_confidence >= read.confidence:
                    category = model_category
                    confidence = max(0.3, min(model_confidence, read.confidence + 0.05))
                else:
                    category = read.category
                    confidence = max(0.3, read.confidence - 0.10)
                notes.append(f"agents disagreed: {review_reason}")
        elif model_category is not None:
            category = model_category
            # No keyword evidence at all: trust the model only enough to route
            # the complaint to a human, never enough to auto-assign a department.
            confidence = min(0.55, model_confidence)
            notes.append("category came from the model with no keyword evidence")
        elif read.category is not None:
            category = read.category
            confidence = read.confidence
            notes.append("category came from deterministic keyword evidence")
        else:
            category = None
            confidence = min(0.3, model_confidence * 0.5)
            notes.append("no category evidence found in the complaint text")

        confidence = round(max(0.0, min(1.0, confidence)), 3)

        # -- urgency and priority ------------------------------------------
        # Deterministic urgency is a floor: a model may escalate a complaint,
        # never quietly de-escalate one (an under-triaged water leak or live
        # wire is exactly the failure this prevents). Escalation is bounded by
        # evidence, though: a model may reach HIGH on its own, but CRITICAL
        # needs corroborating severity evidence in the citizen's own words.
        deterministic_priority = priority_for_urgency(read.urgency_score)
        model_urgency = (
            max(0.0, min(1.0, float(draft.urgency_score)))
            if draft is not None and draft.urgency_score is not None
            else None
        )
        model_priority = _coerce_priority(draft.priority) if draft else None

        urgency = (
            read.urgency_score if model_urgency is None else max(read.urgency_score, model_urgency)
        )
        requested_priority = max(
            (
                band
                for band in (
                    model_priority,
                    priority_for_urgency(model_urgency) if model_urgency is not None else None,
                )
                if band is not None
            ),
            key=_PRIORITY_RANK.__getitem__,
            default=deterministic_priority,
        )

        escalation_reason: str | None = None
        unsupported_escalation = False
        if _PRIORITY_RANK[requested_priority] <= _PRIORITY_RANK[deterministic_priority]:
            priority = deterministic_priority
        elif requested_priority is ComplaintPriority.CRITICAL and not read.escalations:
            priority = MODEL_ESCALATION_CEILING
            urgency = min(urgency, _URGENCY_BAND_CEILING[priority])
            unsupported_escalation = True
            escalation_reason = (
                f"the model's recommendation implied {requested_priority.value}"
                + (f" (its urgency score was {model_urgency:.2f})" if model_urgency else "")
                + " but the complaint contains no corroborating escalation evidence, so the "
                f"priority was capped at {priority.value} and sent to a human"
            )
            notes.append(escalation_reason)
        else:
            priority = requested_priority
            reason = (
                f"the model's recommendation raised the priority to {priority.value}, above the "
                f"deterministic {deterministic_priority.value}"
            )
            if model_urgency is not None:
                reason += f" (model urgency {model_urgency:.2f})"
            if read.escalations:
                reason += f", corroborated by: {', '.join(read.escalations)}"
            escalation_reason = reason
            notes.append(escalation_reason)
        urgency = round(max(urgency, _PRIORITY_FLOOR[priority]), 3)

        # -- explanation ----------------------------------------------------
        reasoning_parts = [
            f"keyword signals: {', '.join(read.matched_signals) or 'none'}"
            + (f" | escalation terms: {', '.join(read.escalations)}" if read.escalations else ""),
        ]
        if draft is not None and draft.reasoning.strip():
            reasoning_parts.append(f"model reasoning: {draft.reasoning.strip()}")
        reasoning_parts.extend(notes)
        if degraded:
            reasoning_parts.append("model unavailable; deterministic keywords only")
        reasoning = ". ".join(part for part in reasoning_parts if part)

        # -- human review ---------------------------------------------------
        # Escalate on low confidence, on a model/deterministic disagreement, or
        # on an escalation the evidence did not support: a split decision or an
        # over-stated severity is exactly what a human should see.
        needs_review = disagreement or unsupported_escalation or confidence < threshold
        if needs_review:
            review_reason = (
                review_reason
                or escalation_reason
                or (f"confidence {confidence:.2f} is below the {threshold:.2f} threshold")
            )

        # -- department: read from the prototype directory, never the model --
        department = _DEPARTMENT_BY_CATEGORY.get(category) if category else None
        department_code = department["code"] if department else None
        guideline = _GUIDELINE_BY_DEPARTMENT_CODE.get(department_code) if department_code else None

        subcategory = draft.subcategory.strip() if draft and draft.subcategory else None
        subcategory = subcategory or read.subcategory

        return ClassificationResult(
            category=category,
            subcategory=subcategory,
            priority=priority,
            urgency_score=urgency,
            confidence=confidence,
            reasoning=reasoning,
            recommended_next_action=self._next_action(payload, draft, category),
            deterministic_priority=deterministic_priority,
            model_priority=model_priority,
            escalation_reason=escalation_reason,
            department_code=department_code,
            department_name=department["name"] if department else None,
            guideline_code=guideline["code"] if guideline else None,
            sla_hours=guideline["sla_hours"] if guideline else None,
            matched_signals=list(read.matched_signals),
            needs_human_review=needs_review,
            review_reason=review_reason if needs_review else None,
            llm_used=draft is not None,
            degraded=degraded,
            latency_ms=call.latency_ms if call else None,
            model_name=call.model if call else None,
        )

    @staticmethod
    def _next_action(
        payload: _ClassificationInput,
        draft: _ClassificationDraft | None,
        category: ComplaintCategory | None,
    ) -> str:
        action = draft.recommended_next_action.strip() if draft else ""
        if not action:
            action = (
                DEFAULT_ACTIONS.get(category, UNDETERMINED_ACTION)
                if category
                else (UNDETERMINED_ACTION)
            )
        if payload.needs_clarification:
            action += " Request the outstanding details from the citizen before dispatch."
        return action


__all__ = [
    "BASE_URGENCY",
    "CATEGORY_KEYWORDS",
    "CRITICAL_ESCALATION_KEYWORDS",
    "DEFAULT_ACTIONS",
    "ESCALATION_KEYWORDS",
    "MAX_MATCHED_SIGNALS",
    "MODEL_ESCALATION_CEILING",
    "RESPONSE_KEYS",
    "SUBCATEGORY_KEYWORDS",
    "SYSTEM_PROMPT",
    "URGENCY_CRITICAL_AT",
    "URGENCY_HIGH_AT",
    "URGENCY_MEDIUM_AT",
    "ClassificationAgent",
    "DeterministicRead",
    "deterministic_read",
    "priority_for_urgency",
]
