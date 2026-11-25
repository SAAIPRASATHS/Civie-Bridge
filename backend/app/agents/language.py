"""Deterministic language detection for citizen complaints.

Detection is deliberately *not* delegated to the language model:

* it is exact and instantaneous, and CPU inference costs ~19 s per call, so
  spending a model call on a Unicode range check is indefensible;
* it makes the stored ``complaints.language`` column reproducible, so dataset
  and agent results can be compared without model variance.

Three languages are distinguished, matching
:class:`app.models.enums.ComplaintLanguage`:

``en``
    English (Latin script, no Tamil signal).
``ta``
    Tamil written in Tamil script (Unicode block U+0B80-U+0BFF).
``ta-latn``
    Tamil written in Latin script (transliterated), e.g. ``kuppai theru``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import ComplaintLanguage

#: Unicode block holding Tamil script characters.
TAMIL_START = 0x0B80
TAMIL_END = 0x0BFF

#: Ratio of Tamil-script characters that identifies Tamil script text. A short
#: English loan-word ("road") inside a Tamil sentence must not flip the result.
TAMIL_SCRIPT_RATIO = 0.12

#: Common Tamil words written in Latin script, plus the romanisation digraphs
#: that distinguish Tamil transliteration from ordinary English.
TRANSLITERATION_MARKERS: frozenset[str] = frozenset(
    {
        "kuppai",
        "kazhivu",
        "setham",
        "thotti",
        "naatram",
        "theru",
        "veethi",
        "salaip",
        "saalai",
        "pallam",
        "paarai",
        "nadaipaathai",
        "thanneer",
        "thanni",
        "kuzhaay",
        "kuzhai",
        "kasivu",
        "vilakku",
        "vilakkil",
        "minsaram",
        "irul",
        "kambam",
        "veedu",
        "pakkam",
        "arukil",
        "nerukku",
        "romba",
        "migavum",
        "illa",
        "illai",
        "aagum",
        "varai",
        "naatkal",
        "vaaram",
        "maatham",
        "nalla",
        "mudiyala",
        "ponnu",
        "porulam",
    }
)

#: Romanisation digraphs that are strong Tamil-Latin signals.
TRANSLITERATION_DIGRAPHS: tuple[str, ...] = (
    "zh",
    "thu",
    "tha",
    "vaa",
    "nee",
    "kku",
    "ppa",
    "tta",
)

_WORD_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    """Detected language plus the evidence behind the decision."""

    language: ComplaintLanguage
    confidence: float
    reason: str


def _tamil_script_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    tamil = sum(1 for char in letters if TAMIL_START <= ord(char) <= TAMIL_END)
    return tamil / len(letters)


def detect_language(text: str) -> LanguageDetection:
    """Classify ``text`` as ``en``, ``ta`` or ``ta-latn``.

    Never raises and never calls the model: the result is a pure function of the
    input, which is what makes it safe to use as the authority on the stored
    ``language`` column.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return LanguageDetection(ComplaintLanguage.EN, 0.0, "empty input")

    ratio = _tamil_script_ratio(cleaned)
    if ratio >= TAMIL_SCRIPT_RATIO:
        confidence = min(1.0, 0.55 + ratio)
        return LanguageDetection(
            ComplaintLanguage.TA,
            round(confidence, 3),
            f"tamil_script_ratio={ratio:.2f}",
        )

    words = _WORD_RE.findall(cleaned.lower())
    markers = [word for word in words if word in TRANSLITERATION_MARKERS]
    if markers:
        confidence = min(0.95, 0.55 + 0.1 * len(markers))
        return LanguageDetection(
            ComplaintLanguage.TA_LATN,
            round(confidence, 3),
            f"transliteration_markers={','.join(sorted(set(markers))[:5])}",
        )

    digraph_hits = 0
    for word in words:
        if len(word) >= 5 and any(digraph in word for digraph in TRANSLITERATION_DIGRAPHS):
            digraph_hits += 1
    # A single romanisation digraph is common in English ("then", "that"), so
    # two independent hits are required before overriding English.
    if digraph_hits >= 2:
        return LanguageDetection(
            ComplaintLanguage.TA_LATN,
            0.6,
            f"transliteration_digraphs={digraph_hits}",
        )

    confidence = 0.9 if len(words) >= 3 else 0.6
    return LanguageDetection(
        ComplaintLanguage.EN,
        confidence,
        f"latin_script_words={len(words)}",
    )
