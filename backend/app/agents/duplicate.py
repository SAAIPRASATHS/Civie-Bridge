"""Agent 3 - Duplicate Detection.

Finds complaints that probably describe the same physical problem, explains
why, and stops there. **This agent never merges, links or rejects a
complaint.** ``complaints.duplicate_of_id`` is only ever written by a human
officer and the ``duplicate`` lifecycle status is terminal, so the worst a
wrong answer here can do is put a pair in front of a reviewer.

Two independent signals are combined:

* **Text similarity** - an offline TF-IDF cosine over character n-grams
  (``scikit-learn``; no downloads, no sentence-transformer weights). Character
  n-grams are deliberate: they survive Tamil agglutination and transliteration
  spelling drift ("thanneer" vs "thanni") where word-level comparison sees two
  unrelated strings. The shared terms behind the score are reported, so the
  number is explainable rather than a black box.
* **Geographic proximity** - the Haversine distance between the two
  coordinates, banded into a 0..1 score. Distance is measured on the sphere, so
  the result does not depend on a map projection.

Thresholds and weights are module constants. A complaint with no coordinates is
compared on text alone against a *higher* bar, because the proximity evidence
that would corroborate a text match is absent.

The model is asked about at most **one borderline pair**, purely to add a second
opinion to the review note. It cannot create a merge, and its opinion never
turns a borderline pair into an action - only into (or out of) a human review.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.agents.errors import AgentLlmError, AgentOutputError
from app.agents.extraction import tokens
from app.agents.llm_client import LlmCallResult, LlmClient, get_default_llm_client
from app.agents.parsing import extract_json_object
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import get_logger
from app.models.enums import ComplaintCategory
from app.schemas.agent import DuplicateCandidate, DuplicateDetectionResult

logger = get_logger(__name__)

#: Mean Earth radius in metres (WGS-84 mean radius).
EARTH_RADIUS_M = 6_371_008.8

#: Signal weights for a comparison that has coordinates on both sides. The text
#: signal carries the decision (70%); proximity corroborates it (30%). These are
#: initial prototype weights, not validated thresholds.
TEXT_WEIGHT = 0.70
GEOGRAPHY_WEIGHT = 0.30
#: Category is a *guard*, not a weighted signal. It never enters the score when
#: both complaints share a category, and it discounts (never vetoes) the score
#: when the two sit in different service categories.
CATEGORY_MISMATCH_PENALTY = 0.85

#: Match score at or above which a pair with coordinates is called a likely duplicate.
DUPLICATE_SCORE_THRESHOLD = 0.62
#: Without coordinates the text has to carry the whole decision.
TEXT_ONLY_THRESHOLD = 0.80
#: Below this text similarity a pair is never a duplicate, however close it is.
MIN_TEXT_SIMILARITY = 0.35
#: Wording this close counts even when the two points are far apart.
NEAR_IDENTICAL_TEXT = 0.90

#: Proximity band: 0 m .. GEOGRAPHY_SAME_SPOT_M scores 1.0, GEOGRAPHY_RADIUS_M and
#: beyond scores 0.0, and the falloff in between is linear.
GEOGRAPHY_SAME_SPOT_M = 150.0
GEOGRAPHY_RADIUS_M = 2_000.0

#: A score in this band is worth one model second opinion.
BORDERLINE_LOW = 0.45

#: Upper bound on the shared terms reported for one candidate.
MAX_SHARED_TERMS = 8

SYSTEM_PROMPT = """\
You are the Duplicate Detection Agent inside CivicBridge AI, a municipal grievance system.
You compare TWO citizen complaints and decide whether they describe the SAME physical
problem at the SAME place, or two different problems.
Return ONE JSON object with exactly these keys:
  same_issue, confidence, reasoning
- "same_issue" is true only when the two complaints are about the same real-world
  defect - not merely the same category.
- "confidence" is a number from 0.0 to 1.0.
- "reasoning" is at most two short sentences.
A human officer makes the final linking decision; your answer is only a second opinion,
so do not overstate it. Reply with the JSON object ONLY.
"""

RESPONSE_KEYS = "same_issue, confidence, reasoning"

#: Words that carry no duplicate signal but are common in complaints.
_EXPLANATION_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "has",
        "have",
        "not",
        "near",
        "our",
        "is",
        "are",
        "was",
        "were",
        "with",
        "from",
        "this",
        "that",
        "been",
        "day",
        "days",
        "week",
        "weeks",
        "there",
        "very",
        "please",
        "about",
        "since",
        "past",
        "last",
    }
)


@dataclass(frozen=True, slots=True)
class ComplaintSnapshot:
    """The comparable view of one complaint.

    Deliberately smaller than the ORM model: duplicate detection needs the
    wording, the category and the coordinates, and nothing else. The pipeline
    builds one from an :class:`~app.schemas.agent.IntakeResult`; the persistence
    layer builds them from stored ``complaints`` rows.
    """

    text: str
    reference_id: str | None = None
    category: ComplaintCategory | None = None
    ward: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def haversine_distance_m(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Great-circle distance between two points, in metres."""
    phi_a, phi_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_phi = math.radians(latitude_b - latitude_a)
    delta_lambda = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, haversine)))


def geographic_score(distance_m: float | None) -> float | None:
    """Band a Haversine distance into a 0..1 proximity score."""
    if distance_m is None:
        return None
    if distance_m <= GEOGRAPHY_SAME_SPOT_M:
        return 1.0
    if distance_m >= GEOGRAPHY_RADIUS_M:
        return 0.0
    span = GEOGRAPHY_RADIUS_M - GEOGRAPHY_SAME_SPOT_M
    return round(1.0 - (distance_m - GEOGRAPHY_SAME_SPOT_M) / span, 3)


# ---------------------------------------------------------------------------
# Text similarity
# ---------------------------------------------------------------------------


def text_similarities(subject: str, candidates: Sequence[str]) -> list[float]:
    """TF-IDF cosine similarity of ``subject`` against each candidate.

    ``char_wb`` 3-5 grams keep the comparison robust across the three supported
    languages and are computed entirely offline. Degenerate input (empty or
    single-character text, which produces no n-grams at all) scores 0.0 rather
    than raising, so one odd complaint cannot break a batch.
    """
    if not candidates:
        return []
    try:
        vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            lowercase=True,
            sublinear_tf=True,
        )
        matrix = vectorizer.fit_transform([subject, *candidates])
    except ValueError as exc:  # empty vocabulary: no usable n-grams in the corpus
        logger.debug("TF-IDF vocabulary empty (%s); text similarity defaults to 0.0", exc)
        return [0.0] * len(candidates)

    scores = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
    return [round(float(value), 3) for value in scores]


def _shared_terms(subject: str, candidate: str) -> list[str]:
    """Content words present in both complaints, for the explanation."""
    left = {
        token
        for token in tokens(subject)
        if len(token) >= 3 and token not in _EXPLANATION_STOPWORDS
    }
    right = {
        token
        for token in tokens(candidate)
        if len(token) >= 3 and token not in _EXPLANATION_STOPWORDS
    }
    return sorted(left & right)[:MAX_SHARED_TERMS]


# ---------------------------------------------------------------------------
# Model second opinion
# ---------------------------------------------------------------------------


class _AdjudicationDraft(BaseModel):
    """The only fields the model may contribute to a borderline decision."""

    model_config = ConfigDict(extra="ignore")

    same_issue: bool | None = None
    confidence: float = 0.5
    reasoning: str = ""


def _build_adjudication_prompt(subject: ComplaintSnapshot, candidate: ComplaintSnapshot) -> str:
    return (
        "Complaint A:\n"
        f'"""\n{subject.text}\n"""\n\n'
        "Complaint B:\n"
        f'"""\n{candidate.text}\n"""\n\n'
        "Return one JSON object with exactly these keys:\n"
        f"{{{RESPONSE_KEYS}}}\n"
        "same_issue must be true or false. confidence is a number between 0.0 and 1.0."
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class DuplicateDetectionAgent:
    """Compare one complaint against stored candidates and rank the matches."""

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

    def run(
        self,
        subject: ComplaintSnapshot,
        candidates: Sequence[ComplaintSnapshot],
    ) -> DuplicateDetectionResult:
        """Rank ``candidates`` against ``subject``. Never merges anything."""
        if not subject.text.strip():
            raise ValueError("subject complaint text must not be empty")

        comparisons = list(candidates)
        similarities = text_similarities(
            subject.text, [candidate.text for candidate in comparisons]
        )

        matches = [
            self._compare(subject, candidate, similarity)
            for candidate, similarity in zip(comparisons, similarities, strict=True)
        ]
        matches.sort(key=lambda match: (match.match_score, match.text_similarity), reverse=True)

        call: LlmCallResult | None = None
        degraded = False
        adjudication = self._pick_borderline(matches)
        if adjudication is not None and self._use_llm and self._llm is not None:
            if self._llm.is_available():
                try:
                    call = self._adjudicate(subject, adjudication)
                except (AgentLlmError, AgentOutputError) as exc:
                    logger.warning("Duplicate adjudication fell back to scores only: %s", exc)
                    degraded = True
            else:
                degraded = True

        likely = [match for match in matches if match.is_likely_duplicate]
        best = matches[0] if matches else None

        review_reason: str | None = None
        if likely:
            review_reason = (
                "a likely duplicate was found; linking the complaints requires human approval "
                "and the agent did not merge or link anything"
            )

        return DuplicateDetectionResult(
            subject_reference_id=subject.reference_id,
            subject_text=subject.text,
            candidates_considered=len(matches),
            matches=matches,
            best_match=best,
            likely_duplicate_found=bool(likely),
            threshold=DUPLICATE_SCORE_THRESHOLD,
            text_only_threshold=TEXT_ONLY_THRESHOLD,
            geographic_radius_m=GEOGRAPHY_RADIUS_M,
            weights={
                "text": TEXT_WEIGHT,
                "geography": GEOGRAPHY_WEIGHT,
                "category_mismatch_penalty": CATEGORY_MISMATCH_PENALTY,
            },
            needs_human_review=bool(likely),
            review_reason=review_reason,
            confidence=self._confidence(best, len(matches)),
            reasoning=self._reasoning(len(matches), best, likely),
            llm_used=call is not None,
            degraded=degraded,
            latency_ms=call.latency_ms if call else None,
            model_name=call.model if call else None,
        )

    def run_intake(
        self,
        intake,
        candidates: Sequence[ComplaintSnapshot],
        *,
        classification=None,
        reference_id: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> DuplicateDetectionResult:
        """Convenience wrapper: build the subject from an intake result."""
        return self.run(
            ComplaintSnapshot(
                text=intake.normalized_description or intake.original_text,
                reference_id=reference_id,
                category=getattr(classification, "category", None),
                ward=intake.ward,
                latitude=latitude,
                longitude=longitude,
            ),
            candidates,
        )

    # -- comparison --------------------------------------------------------

    def _compare(
        self,
        subject: ComplaintSnapshot,
        candidate: ComplaintSnapshot,
        text_similarity: float,
    ) -> DuplicateCandidate:
        reasons: list[str] = []
        shared = _shared_terms(subject.text, candidate.text)

        distance: float | None = None
        if subject.has_coordinates and candidate.has_coordinates:
            distance = haversine_distance_m(
                subject.latitude or 0.0,
                subject.longitude or 0.0,
                candidate.latitude or 0.0,
                candidate.longitude or 0.0,
            )
        geography = geographic_score(distance)

        category_match: bool | None = None
        if subject.category is not None and candidate.category is not None:
            category_match = subject.category is candidate.category

        score = _match_score(text_similarity, geography, category_match)
        likely = _is_likely_duplicate(
            text_similarity=text_similarity,
            geography=geography,
            score=score,
            distance_m=distance,
        )

        reasons.append(f"text similarity {text_similarity:.2f} (offline TF-IDF char n-grams)")
        if shared:
            reasons.append(f"shared terms: {', '.join(shared)}")
        if distance is not None:
            reasons.append(
                f"geographic distance {distance:.0f} m (proximity score {geography:.2f})"
            )
        else:
            reasons.append(
                "no coordinates on one or both complaints, so proximity could not be checked"
            )
        if category_match is True:
            reasons.append(f"same category ({subject.category.value})")
        elif category_match is False:
            reasons.append(
                f"different category ({subject.category.value} vs {candidate.category.value})"
            )
        if subject.ward and candidate.ward:
            reasons.append(
                f"same ward ({subject.ward})"
                if subject.ward == candidate.ward
                else f"different ward ({subject.ward} vs {candidate.ward})"
            )
        reasons.append(f"combined match score {score:.2f}")

        if likely:
            reasons.append(
                "flagged as a likely duplicate for human confirmation; nothing was merged"
            )
        elif distance is not None and geography == 0.0 and text_similarity < NEAR_IDENTICAL_TEXT:
            reasons.append(
                f"below the threshold: the complaints are {distance:.0f} m apart, beyond the "
                f"{GEOGRAPHY_RADIUS_M:.0f} m proximity radius"
            )
        elif geography is None and text_similarity < TEXT_ONLY_THRESHOLD:
            reasons.append(
                f"below the text-only threshold ({text_similarity:.2f} < {TEXT_ONLY_THRESHOLD:.2f})"
            )
        else:
            reasons.append(
                f"below the duplicate threshold ({score:.2f} < {DUPLICATE_SCORE_THRESHOLD:.2f})"
            )

        return DuplicateCandidate(
            reference_id=candidate.reference_id or "unknown",
            text=candidate.text,
            category=candidate.category,
            ward=candidate.ward,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            text_similarity=text_similarity,
            geographic_distance_m=round(distance, 1) if distance is not None else None,
            geographic_score=geography,
            category_match=category_match,
            match_score=score,
            is_likely_duplicate=likely,
            shared_terms=shared,
            reasons=reasons,
        )

    # -- borderline model second opinion -----------------------------------

    @staticmethod
    def _pick_borderline(matches: Sequence[DuplicateCandidate]) -> DuplicateCandidate | None:
        """The strongest pair whose score sits just below the threshold."""
        borderline = [
            match
            for match in matches
            if BORDERLINE_LOW <= match.match_score < DUPLICATE_SCORE_THRESHOLD
            and match.text_similarity >= MIN_TEXT_SIMILARITY
        ]
        return borderline[0] if borderline else None

    def _adjudicate(
        self, subject: ComplaintSnapshot, candidate: DuplicateCandidate
    ) -> LlmCallResult:
        """One model call for one borderline pair; records its opinion in place."""
        assert self._llm is not None  # guarded by the caller
        prompt = _build_adjudication_prompt(
            subject,
            ComplaintSnapshot(
                text=candidate.text,
                reference_id=candidate.reference_id,
                category=candidate.category,
                ward=candidate.ward,
            ),
        )
        call = self._llm.complete(system=SYSTEM_PROMPT, user=prompt)
        attempts = 0

        while True:
            try:
                draft = _AdjudicationDraft.model_validate(extract_json_object(call.text))
            except (AgentOutputError, ValidationError) as exc:
                if attempts >= self._max_repair:
                    raise AgentOutputError(
                        f"duplicate adjudication failed validation after "
                        f"{attempts + 1} attempt(s): {exc}"
                    ) from exc
                attempts += 1
                logger.info("Requesting duplicate adjudication repair (attempt %s)", attempts)
                call = self._llm.complete(
                    system=SYSTEM_PROMPT,
                    user=f"{prompt}\n\nYour previous reply could not be used: {exc}",
                )
                continue
            break

        note = (
            f"model review: same_issue={draft.same_issue}, "
            f"confidence {max(0.0, min(1.0, float(draft.confidence))):.2f}"
        )
        if draft.reasoning.strip():
            note += f" ({draft.reasoning.strip()})"

        if draft.same_issue and draft.confidence >= 0.6:
            # A model opinion may add a pair to the review queue, never to a merge.
            candidate.is_likely_duplicate = True
            candidate.reasons.append(
                f"{note} - promoted to the review queue because the scores alone were borderline"
            )
        else:
            candidate.reasons.append(f"{note} - the borderline pair was left for a human to judge")

        candidate.model_adjudication = note
        return call

    # -- summary -----------------------------------------------------------

    @staticmethod
    def _confidence(best: DuplicateCandidate | None, considered: int) -> float:
        if best is None or considered == 0:
            # Nothing to compare against is not a confident "no duplicate".
            return 0.3
        return round(min(0.9, 0.35 + 0.55 * best.match_score), 3)

    @staticmethod
    def _reasoning(
        considered: int,
        best: DuplicateCandidate | None,
        likely: Sequence[DuplicateCandidate],
    ) -> str:
        if considered == 0:
            return "no stored complaints were supplied to compare against"
        if not best:
            return "no candidates were comparable"
        summary = (
            f"compared {considered} stored complaint(s); the closest match "
            f"({best.reference_id}) scored {best.match_score:.2f} on text similarity "
            f"{best.text_similarity:.2f}"
        )
        if best.geographic_score is not None:
            summary += f" and a proximity score of {best.geographic_score:.2f}"
        if likely:
            summary += f"; {len(likely)} pair(s) are likely duplicates and need human confirmation"
        else:
            summary += "; no pair reached the duplicate threshold"
        return summary + ". The agent did not merge, link or reject any complaint."


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _match_score(
    text_similarity: float,
    geography: float | None,
    category_match: bool | None,
) -> float:
    """Combine the signals: 70% text / 30% proximity, then the category guard.

    When proximity is unavailable the text carries the whole score, because a
    missing coordinate must not automatically disqualify a pair. A cross-category
    mismatch discounts the score; it never by itself rejects a pair, and it is
    skipped entirely when either complaint has no category.
    """
    if geography is None:
        score = text_similarity
    else:
        score = TEXT_WEIGHT * text_similarity + GEOGRAPHY_WEIGHT * geography
    if category_match is False:
        score *= CATEGORY_MISMATCH_PENALTY
    return round(max(0.0, min(1.0, score)), 3)


def _is_likely_duplicate(
    *,
    text_similarity: float,
    geography: float | None,
    score: float,
    distance_m: float | None,
) -> bool:
    if text_similarity < MIN_TEXT_SIMILARITY:
        # Proximity alone never makes a duplicate.
        return False
    if geography is None:
        return text_similarity >= TEXT_ONLY_THRESHOLD
    if geography == 0.0 and distance_m is not None:
        # Far apart: only near-identical wording keeps the pair in play.
        return text_similarity >= NEAR_IDENTICAL_TEXT
    return score >= DUPLICATE_SCORE_THRESHOLD


__all__ = [
    "BORDERLINE_LOW",
    "CATEGORY_MISMATCH_PENALTY",
    "DUPLICATE_SCORE_THRESHOLD",
    "EARTH_RADIUS_M",
    "GEOGRAPHY_RADIUS_M",
    "MIN_TEXT_SIMILARITY",
    "NEAR_IDENTICAL_TEXT",
    "RESPONSE_KEYS",
    "SYSTEM_PROMPT",
    "TEXT_ONLY_THRESHOLD",
    "ComplaintSnapshot",
    "DuplicateDetectionAgent",
    "geographic_score",
    "haversine_distance_m",
    "text_similarities",
]
