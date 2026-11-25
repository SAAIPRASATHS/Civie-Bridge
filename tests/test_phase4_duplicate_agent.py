"""Phase 4 tests: the offline duplicate-detection agent.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase4_duplicate_agent.py -v

Everything here is fully offline and deterministic - no Ollama, no database.
The cases the agent is most likely to get wrong are covered deliberately:

* **false positives** - near-identical wording at *different* places, and two
  complaints that share a category but describe different problems;
* **false negatives** - the same problem re-reported in different words,
  including Tamil transliteration spelling drift;
* **missing coordinates** - which must be compared on text alone, not dropped;
* **Tamil transliteration** - the hardest text case, and one the benchmark
  already documents as a known limitation.

The tests also assert the structural safety property: the agent only ever
*recommends* a pair for human review - it never merges, links or rejects.
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

from app.agents.duplicate import (
    CATEGORY_MISMATCH_PENALTY,
    DUPLICATE_SCORE_THRESHOLD,
    GEOGRAPHY_RADIUS_M,
    MIN_TEXT_SIMILARITY,
    TEXT_ONLY_THRESHOLD,
    ComplaintSnapshot,
    DuplicateDetectionAgent,
    geographic_score,
    haversine_distance_m,
    text_similarities,
)
from app.agents.llm_client import LlmCallResult
from app.models.enums import ComplaintCategory

AGENT = DuplicateDetectionAgent(use_llm=False)

#: Synthetic coordinates: Anna Nagar and Besant Nagar are ~11 km apart.
ANNA_NAGAR = (13.0850, 80.2100)
BESANT_NAGAR = (13.0000, 80.2700)

GARBAGE = "Garbage has not been collected on our street for five days."
WATER = "No drinking water supply in our street for five days."


def _snapshot(
    text: str,
    *,
    category: ComplaintCategory | None = None,
    coordinates: tuple[float, float] | None = None,
    ward: str | None = None,
    reference_id: str | None = None,
) -> ComplaintSnapshot:
    return ComplaintSnapshot(
        text=text,
        reference_id=reference_id,
        category=category,
        ward=ward,
        latitude=coordinates[0] if coordinates else None,
        longitude=coordinates[1] if coordinates else None,
    )


class _FakeLlm:
    """Minimal ``LlmClient`` returning one scripted adjudication."""

    def __init__(self, response: str, *, available: bool = True) -> None:
        self.response = response
        self.available = available
        self.calls: list[dict[str, str]] = []

    def is_available(self) -> bool:
        return self.available

    def complete(self, *, system: str, user: str) -> LlmCallResult:
        self.calls.append({"system": system, "user": user})
        return LlmCallResult(text=self.response, model="fake", provider="fake", latency_ms=1.0)


# ---------------------------------------------------------------------------
# 1. Geometry
# ---------------------------------------------------------------------------


def test_haversine_is_zero_for_the_same_point() -> None:
    assert haversine_distance_m(*ANNA_NAGAR, *ANNA_NAGAR) == pytest.approx(0.0, abs=1e-6)


def test_haversine_matches_a_known_distance() -> None:
    distance = haversine_distance_m(*ANNA_NAGAR, *BESANT_NAGAR)

    assert 10_000 < distance < 13_000


@pytest.mark.parametrize(
    ("distance_m", "expected"),
    [
        (0.0, 1.0),
        (150.0, 1.0),
        (1_075.0, 0.5),
        (2_000.0, 0.0),
        (9_999.0, 0.0),
    ],
)
def test_geographic_score_banding(distance_m: float, expected: float) -> None:
    assert geographic_score(distance_m) == pytest.approx(expected, abs=0.001)


def test_geographic_score_is_none_without_a_distance() -> None:
    assert geographic_score(None) is None


# ---------------------------------------------------------------------------
# 2. Text similarity
# ---------------------------------------------------------------------------


def test_identical_text_scores_one() -> None:
    assert text_similarities(GARBAGE, [GARBAGE]) == [1.0]


def test_unrelated_text_scores_below_the_minimum() -> None:
    scores = text_similarities(GARBAGE, ["The street lights are not working at night."])

    assert scores[0] < MIN_TEXT_SIMILARITY


def test_tamil_transliteration_variants_stay_similar() -> None:
    """Character n-grams must survive transliteration spelling drift."""
    scores = text_similarities(
        "Engal theruvil aindhu naatkalaga kuppai allapadavillai.",
        ["Enga theruvil aindhu naatkal kuppai alla padavillai."],
    )

    assert 0.5 <= scores[0] < 1.0


def test_no_candidates_returns_empty_list() -> None:
    assert text_similarities(GARBAGE, []) == []


def test_degenerate_text_scores_zero_instead_of_raising() -> None:
    """A single character produces no 3-grams; the batch must not crash."""
    scores = text_similarities(GARBAGE, ["a"])

    assert scores == [0.0]


# ---------------------------------------------------------------------------
# 3. True positives - the same problem, the same place
# ---------------------------------------------------------------------------


def test_identical_complaints_at_the_same_place_are_flagged() -> None:
    subject = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )
    candidate = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )

    result = AGENT.run(subject, [candidate])

    assert result.likely_duplicate_found is True
    assert result.matches[0].is_likely_duplicate is True
    assert result.needs_human_review is True


def test_re_report_with_extra_wording_is_flagged() -> None:
    subject = _snapshot(GARBAGE, coordinates=ANNA_NAGAR)
    candidate = _snapshot(f"{GARBAGE} Reported again by another resident.", coordinates=ANNA_NAGAR)

    result = AGENT.run(subject, [candidate])

    assert result.matches[0].is_likely_duplicate is True


# ---------------------------------------------------------------------------
# 4. False positives - similar text, different problem or place
# ---------------------------------------------------------------------------


def test_near_identical_text_far_apart_is_not_flagged() -> None:
    """The classic trap: one word differs, but the places are 11 km apart."""
    subject = _snapshot(
        "A large pothole has formed near the Anna Nagar bus stop.",
        category=ComplaintCategory.ROAD_MAINTENANCE,
        coordinates=ANNA_NAGAR,
    )
    candidate = _snapshot(
        "A large pothole has formed near the Besant Nagar bus stop.",
        category=ComplaintCategory.ROAD_MAINTENANCE,
        coordinates=BESANT_NAGAR,
    )

    match = AGENT.run(subject, [candidate]).matches[0]

    assert match.geographic_distance_m is not None
    assert match.geographic_distance_m > GEOGRAPHY_RADIUS_M
    assert match.geographic_score == 0.0
    assert match.is_likely_duplicate is False


def test_same_place_different_category_is_discounted_by_the_guard() -> None:
    """A cross-category pair scores below the same-category equivalent."""
    subject = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )

    same_category = AGENT.run(
        subject,
        [_snapshot(GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR)],
    ).matches[0]
    cross_category = AGENT.run(
        subject,
        [_snapshot(GARBAGE, category=ComplaintCategory.WATER_SUPPLY, coordinates=ANNA_NAGAR)],
    ).matches[0]

    assert same_category.category_match is True
    assert cross_category.category_match is False
    assert cross_category.match_score == pytest.approx(
        same_category.match_score * CATEGORY_MISMATCH_PENALTY, abs=0.001
    )


def test_shared_sentence_frame_does_not_merge_unrelated_complaints() -> None:
    subject = _snapshot("There is a water leakage problem in our area.")
    candidate = _snapshot("There is a garbage problem in our area.")

    assert AGENT.run(subject, [candidate]).matches[0].is_likely_duplicate is False


# ---------------------------------------------------------------------------
# 5. Missing coordinates
# ---------------------------------------------------------------------------


def test_missing_coordinates_are_not_disqualified_when_the_text_matches() -> None:
    """No fix on either side: the pair is judged on text alone."""
    subject = _snapshot(WATER, category=ComplaintCategory.WATER_SUPPLY)
    candidate = _snapshot(WATER, category=ComplaintCategory.WATER_SUPPLY)

    match = AGENT.run(subject, [candidate]).matches[0]

    assert match.geographic_distance_m is None
    assert match.geographic_score is None
    assert match.is_likely_duplicate is True


def test_missing_coordinates_raise_the_text_bar() -> None:
    """Without proximity corroboration, a moderate text match is not enough."""
    subject = _snapshot(GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT)
    candidate = _snapshot(
        "The garbage bin near our street is overflowing again.",
        category=ComplaintCategory.WASTE_MANAGEMENT,
    )

    match = AGENT.run(subject, [candidate]).matches[0]

    assert match.text_similarity < TEXT_ONLY_THRESHOLD
    assert match.is_likely_duplicate is False


def test_one_missing_coordinate_falls_back_to_text_only() -> None:
    subject = _snapshot(GARBAGE, coordinates=ANNA_NAGAR)
    candidate = _snapshot(GARBAGE)

    match = AGENT.run(subject, [candidate]).matches[0]

    assert match.geographic_distance_m is None
    assert match.geographic_score is None
    assert match.is_likely_duplicate is True


# ---------------------------------------------------------------------------
# 6. Tamil transliteration (including the documented false negative)
# ---------------------------------------------------------------------------


def test_transliterated_duplicate_is_flagged_when_coordinates_corroborate() -> None:
    subject = _snapshot(
        "Engal theruvil aindhu naatkalaga kuppai allapadavillai.",
        category=ComplaintCategory.WASTE_MANAGEMENT,
        coordinates=ANNA_NAGAR,
    )
    candidate = _snapshot(
        "Enga theruvil aindhu naatkal kuppai alla padavillai.",
        category=ComplaintCategory.WASTE_MANAGEMENT,
        coordinates=ANNA_NAGAR,
    )

    assert AGENT.run(subject, [candidate]).matches[0].is_likely_duplicate is True


def test_transliterated_short_complaint_without_coordinates_is_a_known_false_negative() -> None:
    """Documents a real limitation: short transliterated text with no fix.

    The benchmark (``ml/duplicate_detection``) reports this as the single
    false negative in the hand-reviewed set. The pair is *not* silently
    merged - it simply does not reach the text-only bar - which is the
    conservative failure the design accepts.
    """
    subject = _snapshot("Kudineer viniyogam illai moonru naatkalaga.")
    candidate = _snapshot("Kudineer viniyogam illa moonru naatkal.")

    match = AGENT.run(subject, [candidate]).matches[0]

    assert match.text_similarity < TEXT_ONLY_THRESHOLD
    assert match.is_likely_duplicate is False


# ---------------------------------------------------------------------------
# 7. Ranking, empty input and the no-merge guarantee
# ---------------------------------------------------------------------------


def test_best_match_is_the_highest_scoring_candidate() -> None:
    subject = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )
    weak = _snapshot("A street light is broken.", coordinates=BESANT_NAGAR, reference_id="CB-weak")
    strong = _snapshot(GARBAGE, coordinates=ANNA_NAGAR, reference_id="CB-strong")

    result = AGENT.run(subject, [weak, strong])

    assert result.best_match is not None
    assert result.best_match.reference_id == "CB-strong"
    assert result.candidates_considered == 2


def test_no_candidates_is_not_a_confident_no_duplicate() -> None:
    result = AGENT.run(_snapshot(GARBAGE), [])

    assert result.candidates_considered == 0
    assert result.matches == []
    assert result.best_match is None
    assert result.likely_duplicate_found is False
    assert result.confidence < 0.5


def test_empty_subject_text_is_rejected() -> None:
    with pytest.raises(ValueError):
        AGENT.run(_snapshot("   "), [_snapshot(GARBAGE)])


def test_agent_never_merges_only_recommends_review() -> None:
    subject = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )
    candidate = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )

    result = AGENT.run(subject, [candidate])

    assert result.linking_requires_human_approval is True
    assert result.review_reason is not None
    # The only side effect is a flag on a result object, never a merge.
    assert result.matches[0].is_likely_duplicate is True
    assert not hasattr(result, "merged")


def test_every_match_reports_an_explanation() -> None:
    subject = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )
    candidate = _snapshot(
        GARBAGE, category=ComplaintCategory.WASTE_MANAGEMENT, coordinates=ANNA_NAGAR
    )

    match = AGENT.run(subject, [candidate]).matches[0]

    assert match.reasons
    assert match.shared_terms
    assert any("text similarity" in reason for reason in match.reasons)


def test_result_echoes_the_scoring_configuration() -> None:
    result = AGENT.run(_snapshot(GARBAGE), [])

    assert result.weights["text"] == 0.70
    assert result.weights["geography"] == 0.30
    assert result.threshold == DUPLICATE_SCORE_THRESHOLD
    assert result.text_only_threshold == TEXT_ONLY_THRESHOLD


# ---------------------------------------------------------------------------
# 8. The model second opinion can only add a pair to the review queue
# ---------------------------------------------------------------------------


def test_borderline_pair_is_promoted_to_review_but_never_merged() -> None:
    """A model 'same_issue' verdict adds a pair to review; it cannot merge."""
    # Moderate text overlap at the same place lands the score in the borderline band.
    subject = _snapshot(
        "The waste bin near the bus stop is overflowing and has not been cleared for a week.",
        category=ComplaintCategory.WASTE_MANAGEMENT,
        coordinates=ANNA_NAGAR,
    )
    candidate = _snapshot(
        "Garbage has been piling up near the bus stop for a week.",
        category=ComplaintCategory.WASTE_MANAGEMENT,
        coordinates=ANNA_NAGAR,
    )

    scored_only = AGENT.run(subject, [candidate]).matches[0]

    fake = _FakeLlm(json.dumps({"same_issue": True, "confidence": 0.9, "reasoning": "same bin"}))
    adjudicated = (
        DuplicateDetectionAgent(llm=fake, use_llm=True).run(subject, [candidate]).matches[0]
    )

    assert scored_only.match_score < DUPLICATE_SCORE_THRESHOLD
    assert adjudicated.is_likely_duplicate is True
    assert adjudicated.model_adjudication is not None
    assert len(fake.calls) == 1


def test_model_is_not_called_when_the_model_is_offline() -> None:
    subject = _snapshot("Garbage overflowing near the bus stop.", coordinates=ANNA_NAGAR)
    candidate = _snapshot("Garbage piling up beside the bus stop.", coordinates=ANNA_NAGAR)
    fake = _FakeLlm("{}", available=False)

    result = DuplicateDetectionAgent(llm=fake, use_llm=True).run(subject, [candidate])

    assert fake.calls == []
    assert result.llm_used is False
