"""Deterministic extraction and grounding helpers.

Everything here is pure Python: no model calls. Two jobs:

1. **Extraction** - pull duration, location and landmark mentions out of English,
   Tamil and Tamil-transliterated text. This is the evidence the intake agent
   uses when the model is unavailable, and the cross-check it uses when the
   model *is* available.
2. **Grounding** - decide whether a value the model produced is actually
   supported by the citizen's own words. The intake agent discards any location,
   landmark or duration that is not grounded, which is how "never invent
   locations or complaint details" is enforced in code rather than in a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\u0b80-\u0bff]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    lowered = (text or "").lower()
    return _WHITESPACE_RE.sub(" ", _PUNCT_RE.sub(" ", lowered)).strip()


def tokens(text: str) -> list[str]:
    return [token for token in normalise(text).split(" ") if token]


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------

_NUMBER_WORDS: dict[str, float] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "oru": 1,
    "onru": 1,
    "irandu": 2,
    "rendu": 2,
    "moonru": 3,
    "naalu": 4,
    "naangu": 4,
    "ainthu": 5,
    "aaru": 6,
    "ezhu": 7,
    "ettu": 8,
    "onbadhu": 9,
    "pathu": 10,
}

#: ``unit token -> days``. Tamil script, Tamil transliteration and English are
#: all listed so one regex covers the three supported languages.
_UNIT_DAYS: dict[str, float] = {
    "hour": 1 / 24,
    "hours": 1 / 24,
    "hra": 1 / 24,
    "day": 1.0,
    "days": 1.0,
    "week": 7.0,
    "weeks": 7.0,
    "month": 30.0,
    "months": 30.0,
    "year": 365.0,
    "years": 365.0,
    "naal": 1.0,
    "naatkal": 1.0,
    "naalai": 1.0,
    "vaaram": 7.0,
    "maatham": 30.0,
    "mani": 1 / 24,
    "மணி": 1 / 24,
    "நாள்": 1.0,
    "நாட்கள்": 1.0,
    "வாரம்": 7.0,
    "மாதம்": 30.0,
    "வருடம்": 365.0,
}

_UNIT_PATTERN = "|".join(sorted(_UNIT_DAYS, key=len, reverse=True))
_NUMBER_PATTERN = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))

#: ``(?![a-z])`` stands in for ``\b`` so Tamil script units match too. Tamil
#: agglutinates and its unit words end in a virama or a vowel sign, neither of
#: which is a word character, so a trailing ``\b`` is never satisfied after
#: them. The lookahead still stops "day" matching inside "daysomething".
_DURATION_RE = re.compile(
    rf"(?P<value>\d+(?:\.\d+)?|{_NUMBER_PATTERN})\s*(?P<unit>{_UNIT_PATTERN})(?![a-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DurationMention:
    """A duration found in the complaint text."""

    days: float
    text: str


def extract_duration(text: str) -> DurationMention | None:
    """Return the first duration mentioned, converted to days."""
    match = _DURATION_RE.search(text or "")
    if not match:
        return None

    raw_value = match.group("value").lower()
    value = (
        float(raw_value) if raw_value.replace(".", "", 1).isdigit() else _NUMBER_WORDS[raw_value]
    )
    unit_days = _UNIT_DAYS[match.group("unit").lower()]
    return DurationMention(days=round(value * unit_days, 4), text=match.group(0).strip())


# ---------------------------------------------------------------------------
# Location and landmarks
# ---------------------------------------------------------------------------

_LOCATION_HINTS: tuple[str, ...] = (
    "near",
    "opposite",
    "behind",
    "beside",
    "next to",
    "in front of",
    "around",
    "at",
    "in",
    "on",
    "arukil",
    "pakkathil",
    "edhiril",
    "pinnaal",
    "அருகில்",
    "அருகே",
    "பக்கத்தில்",
    "எதிரில்",
    "முன்பு",
)


def _hint_pattern(hint: str) -> re.Pattern[str]:
    """Compile one location hint with the matching rule its script needs.

    Latin hints are short prepositions, so they must match on a word boundary:
    without one, ``"in"`` matches inside ``"bin"`` and everything after it is
    swallowed as an address. Tamil agglutinates - a locative hint such as
    அருகில் is a suffix of longer words - so Tamil hints stay substring matches.
    """
    escaped = re.escape(hint)
    if hint.isascii():
        return re.compile(rf"(?<![a-z]){escaped}(?![a-z])")
    return re.compile(escaped)


_LOCATION_HINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _hint_pattern(hint) for hint in _LOCATION_HINTS
)

#: Street furniture / place words that make a phrase a usable address.
_LOCATION_NOUNS: tuple[str, ...] = (
    "street",
    "road",
    "lane",
    "cross",
    "avenue",
    "nagar",
    "colony",
    "junction",
    "bus stop",
    "school",
    "hospital",
    "temple",
    "market",
    "park",
    "bridge",
    "ward",
    "block",
    "theru",
    "veethi",
    "salai",
    "saalai",
    "kadai",
    "koil",
    "palar",
    "தெரு",
    "வீதி",
    "சாலை",
    "கோவில்",
    "பள்ளி",
    "மருத்துவமனை",
    "சந்தை",
    "பேருந்து",
    "வார்டு",
)

_WARD_RE = re.compile(r"\b(?:ward|வார்டு)\s*(?:no\.?|number|எண்)?\s*(\d{1,3})\b", re.IGNORECASE)

#: Words that terminate a location phrase.
_CLAUSE_BREAK = {"and", "but", "because", "since", "so", "which", "that", "மற்றும்", "ஆனால்"}


def extract_ward(text: str) -> str | None:
    """Return ``"Ward 10"`` when the complaint names a ward number."""
    match = _WARD_RE.search(text or "")
    return f"Ward {match.group(1)}" if match else None


def extract_location_phrase(text: str) -> str | None:
    """Return the most specific location phrase found, or ``None``.

    Deliberately conservative: a phrase is only returned when it follows a
    location hint *and* contains a place noun, so "3 days" is never mistaken for
    an address.
    """
    if not text:
        return None

    lowered = text.lower()
    best: str | None = None
    for pattern in _LOCATION_HINT_PATTERNS:
        for match in pattern.finditer(lowered):
            tail = text[match.end() :]
            phrase = _clean_phrase(tail)
            if phrase and _contains_place_noun(phrase) and len(phrase) > len(best or ""):
                best = phrase
    return best


def _clean_phrase(tail: str) -> str | None:
    """Trim a trailing clause down to at most six words, stopping at punctuation."""
    snippet = re.split(r"[.,;:!?\n(]", tail, maxsplit=1)[0].strip()
    words: list[str] = []
    for word in snippet.split():
        if word.lower() in _CLAUSE_BREAK:
            break
        words.append(word)
        if len(words) == 6:
            break
    phrase = " ".join(words).strip(" ,.;:-")
    return phrase or None


def _contains_place_noun(phrase: str) -> bool:
    lowered = phrase.lower()
    return any(noun in lowered for noun in _LOCATION_NOUNS) or bool(re.search(r"\d", phrase))


def extract_landmarks(text: str) -> list[str]:
    """Extract landmark-ish noun phrases (school, temple, bus stop, ...).

    Returns short phrases, de-duplicated, capped at three entries.
    """
    if not text:
        return []

    found: list[str] = []
    lowered = text.lower()
    for noun in _LOCATION_NOUNS:
        index = lowered.find(noun)
        if index == -1:
            continue
        head = text[max(0, index - 24) : index + len(noun)]
        phrase = _clean_phrase(head)
        if not phrase:
            continue
        short = " ".join(phrase.split()[-3:])
        if short and short.lower() not in {item.lower() for item in found}:
            found.append(short)
    return found[:3]


def looks_like_location(text: str) -> bool:
    """Whether the text carries an addressable location at all."""
    return bool(extract_ward(text) or extract_location_phrase(text))


def location_is_specific(phrase: str | None, source: str) -> bool:
    """Whether a location phrase pins down a spot a field crew can be sent to.

    ``"our street"`` is a mention but not an address; ``"Ward 10"`` or
    ``"3rd Cross Street, Anna Nagar"`` is. Used to decide whether the intake
    agent must ask for the exact location.
    """
    if not phrase:
        return False
    if re.search(r"\d", phrase):
        return True
    if len(phrase.split()) >= 3:
        return True
    # A capitalised token in the citizen's own wording is normally a proper noun.
    source_words = {word.strip(".,;:") for word in (source or "").split()}
    return any(token[:1].isupper() and token in source_words for token in phrase.split())


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------

_TAMIL_CHAR_RANGE = (0x0B80, 0x0BFF)


def has_tamil_script(text: str | None) -> bool:
    """Whether ``text`` contains at least one Tamil-script character."""
    return any(_TAMIL_CHAR_RANGE[0] <= ord(char) <= _TAMIL_CHAR_RANGE[1] for char in (text or ""))


def grounding_ratio(candidate: str, source: str) -> float:
    """How much of ``candidate`` is traceable to ``source`` (0.0 - 1.0).

    Latin text is compared token-wise; Tamil script is compared character-wise
    because Tamil agglutinates heavily and word forms rarely match exactly.
    """
    candidate_norm = normalise(candidate)
    source_norm = normalise(source)
    if not candidate_norm:
        return 0.0
    if candidate_norm in source_norm:
        return 1.0

    source_tokens = set(source_norm.split())
    candidate_tokens = candidate_norm.split()
    token_hits = sum(1 for token in candidate_tokens if token in source_tokens)
    token_ratio = token_hits / len(candidate_tokens)

    if has_tamil_script(candidate):
        source_chars = {char for char in source_norm if char.strip()}
        candidate_chars = [char for char in candidate_norm if char.strip()]
        char_ratio = sum(1 for char in candidate_chars if char in source_chars) / len(
            candidate_chars
        )
        return max(token_ratio, char_ratio)

    return token_ratio


def is_grounded(candidate: str, source: str, *, minimum: float = 0.6) -> bool:
    """Whether ``candidate`` is supported by the citizen's own words."""
    return grounding_ratio(candidate, source) >= minimum


# ---------------------------------------------------------------------------
# Clarification questions
# ---------------------------------------------------------------------------

#: Canonical missing-information keys the agents reason about.
MISSING_EXACT_LOCATION = "exact_location"
MISSING_LANDMARK = "landmark"
MISSING_DURATION = "duration"
MISSING_EVIDENCE = "evidence_photo"
MISSING_DETAIL = "description_detail"

MISSING_ITEM_QUESTIONS: dict[str, str] = {
    MISSING_EXACT_LOCATION: (
        "Please share the exact location - street name, ward number or a nearby "
        "landmark - so the field team can find the spot."
    ),
    MISSING_LANDMARK: ("Is there a well-known landmark, building or junction near the problem?"),
    MISSING_DURATION: "How long has this problem been continuing?",
    MISSING_EVIDENCE: (
        "If possible, please attach a photo of the problem; it speeds up verification."
    ),
    MISSING_DETAIL: (
        "Could you describe the problem in a little more detail - what exactly is "
        "wrong and what has it affected?"
    ),
}

#: Free-text (often model-produced) missing items mapped onto the canonical keys.
_MISSING_SYNONYMS: dict[str, tuple[str, ...]] = {
    MISSING_EXACT_LOCATION: ("location", "address", "where", "place", "spot", "இடம்"),
    MISSING_LANDMARK: ("landmark", "reference point", "junction", "அடையாளம்"),
    MISSING_DURATION: ("duration", "how long", "since when", "time period", "நேரம்"),
    MISSING_EVIDENCE: ("photo", "image", "picture", "evidence", "புகைப்படம்"),
    MISSING_DETAIL: ("detail", "description", "clarity", "ambiguous", "vague"),
}


def canonical_missing_item(raw: str) -> str:
    """Map an arbitrary missing-information label onto a canonical key."""
    lowered = (raw or "").strip().lower()
    if not lowered:
        return MISSING_DETAIL
    for canonical, synonyms in _MISSING_SYNONYMS.items():
        if any(synonym in lowered for synonym in synonyms):
            return canonical
    return lowered


def question_for(missing_item: str) -> str:
    """Clarification question for a canonical (or unknown) missing item."""
    canonical = canonical_missing_item(missing_item)
    return MISSING_ITEM_QUESTIONS.get(
        canonical,
        f"Could you please provide more information about: {missing_item}?",
    )
