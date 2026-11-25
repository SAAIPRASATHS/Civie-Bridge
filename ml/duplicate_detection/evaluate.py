"""Offline benchmark for the Duplicate Detection agent.

Runs the **offline** duplicate detector (TF-IDF cosine over word and character
n-grams + Haversine proximity; no model calls, ``use_llm=False``) against two
labelled sets and writes a report:

* ``labelled_pairs.json`` - a small, hand-reviewed set of English, Tamil and
  Tamil-transliterated pairs, including deliberate false-positive traps and
  missing-coordinate cases.
* the planted duplicates in ``datasets/complaints_synthetic.json`` - a larger
  sanity check whose ground truth is ``duplicate_of_reference_id``.

Run from the repository root or the ``ml/duplicate_detection`` directory:

    python ml/duplicate_detection/evaluate.py

It writes ``report.json`` and ``report.md`` next to this file and prints a
one-screen summary. Nothing here mutates the database or calls Ollama.
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.duplicate import ComplaintSnapshot, DuplicateDetectionAgent  # noqa: E402
from app.models.enums import ComplaintCategory  # noqa: E402

HERE = Path(__file__).resolve().parent
PAIRS_PATH = HERE / "labelled_pairs.json"
SYNTHETIC_PATH = REPO_ROOT / "datasets" / "complaints_synthetic.json"
REPORT_JSON = HERE / "report.json"
REPORT_MD = HERE / "report.md"

POSITIVE = "duplicate"
NEGATIVE = "distinct"

#: Deterministic sampling for the synthetic negative pairs.
SYNTHETIC_SEED = 20261125


@dataclass(slots=True)
class LabelledPair:
    """One labelled comparison, ready to hand to the agent."""

    pair_id: str
    label: str
    language: str
    note: str
    subject: ComplaintSnapshot
    candidate: ComplaintSnapshot


@dataclass(slots=True)
class Outcome:
    """What the agent decided for one pair, plus the signals behind it."""

    pair_id: str
    label: str
    language: str
    predicted: bool
    match_score: float
    text_similarity: float
    geographic_score: float | None
    distance_m: float | None
    proximity_available: bool
    category_relation: str

    @property
    def correct(self) -> bool:
        return self.predicted == (self.label == POSITIVE)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _snapshot(payload: dict[str, Any], *, text_key: str = "text") -> ComplaintSnapshot:
    category = payload.get("category")
    return ComplaintSnapshot(
        text=payload[text_key],
        reference_id=payload.get("reference_id"),
        category=ComplaintCategory(category) if category else None,
        ward=payload.get("ward"),
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
    )


def load_labelled_pairs(path: Path = PAIRS_PATH) -> list[LabelledPair]:
    """The hand-reviewed pairs from ``labelled_pairs.json``."""
    document = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[LabelledPair] = []
    for entry in document["pairs"]:
        label = entry["label"]
        if label not in {POSITIVE, NEGATIVE}:
            raise ValueError(f"{entry['id']}: unknown label {label!r}")
        pairs.append(
            LabelledPair(
                pair_id=entry["id"],
                label=label,
                language=entry.get("language", "unknown"),
                note=entry.get("note", ""),
                subject=_snapshot(entry["subject"]),
                candidate=_snapshot(entry["candidate"]),
            )
        )
    return pairs


def load_synthetic_pairs(
    path: Path = SYNTHETIC_PATH, *, negatives_per_positive: int = 2
) -> list[LabelledPair]:
    """Planted-duplicate ground truth from the synthetic dataset.

    Positives are the planted copies (``duplicate_of_reference_id`` set).
    Negatives are same-category complaints from a *different* ward, which is
    the hardest honest negative: similar wording, wrong place.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    by_reference = {row["reference_id"]: row for row in rows}
    primaries = [row for row in rows if not row.get("duplicate_of_reference_id")]
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in primaries:
        by_category[row["category"]].append(row)

    rng = random.Random(SYNTHETIC_SEED)
    pairs: list[LabelledPair] = []
    for row in rows:
        original_ref = row.get("duplicate_of_reference_id")
        if not original_ref or original_ref not in by_reference:
            continue
        original = by_reference[original_ref]
        pairs.append(
            LabelledPair(
                pair_id=f"planted-{row['reference_id']}",
                label=POSITIVE,
                language=row["language"],
                note=f"planted copy of {original_ref}",
                subject=_snapshot(original, text_key="description"),
                candidate=_snapshot(row, text_key="description"),
            )
        )

        pool = [
            candidate
            for candidate in by_category[row["category"]]
            if candidate["reference_id"] != original_ref and candidate["ward"] != original["ward"]
        ]
        for offset in range(min(negatives_per_positive, len(pool))):
            other = pool.pop(rng.randrange(len(pool))) if pool else None
            if other is None:
                break
            negative = LabelledPair(
                pair_id=f"planted-neg-{row['reference_id']}-{offset}",
                label=NEGATIVE,
                language=row["language"],
                note="same category, different ward",
                subject=_snapshot(original, text_key="description"),
                candidate=_snapshot(other, text_key="description"),
            )
            pairs.append(negative)
    return pairs


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _category_relation(subject: ComplaintSnapshot, candidate: ComplaintSnapshot) -> str:
    if subject.category is None or candidate.category is None:
        return "unknown"
    return "same" if subject.category is candidate.category else "different"


def evaluate_pair(agent: DuplicateDetectionAgent, pair: LabelledPair) -> Outcome:
    result = agent.run(pair.subject, [pair.candidate])
    match = result.matches[0]
    return Outcome(
        pair_id=pair.pair_id,
        label=pair.label,
        language=pair.language,
        predicted=match.is_likely_duplicate,
        match_score=match.match_score,
        text_similarity=match.text_similarity,
        geographic_score=match.geographic_score,
        distance_m=match.geographic_distance_m,
        proximity_available=match.geographic_score is not None,
        category_relation=_category_relation(pair.subject, pair.candidate),
    )


def evaluate(agent: DuplicateDetectionAgent, pairs: list[LabelledPair]) -> list[Outcome]:
    return [evaluate_pair(agent, pair) for pair in pairs]


def metrics(outcomes: list[Outcome]) -> dict[str, Any]:
    true_positive = sum(1 for o in outcomes if o.label == POSITIVE and o.predicted)
    false_positive = sum(1 for o in outcomes if o.label == NEGATIVE and o.predicted)
    false_negative = sum(1 for o in outcomes if o.label == POSITIVE and not o.predicted)
    true_negative = sum(1 for o in outcomes if o.label == NEGATIVE and not o.predicted)

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2 * precision * recall, precision + recall) if precision and recall else 0.0
    total = len(outcomes)
    return {
        "pairs": total,
        "positives": true_positive + false_negative,
        "negatives": true_negative + false_positive,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "accuracy": round(_ratio(true_positive + true_negative, total), 3),
        "flagged_for_review": true_positive + false_positive,
    }


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def stratified(outcomes: list[Outcome], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        buckets[_stratum(outcome, key)].append(outcome)
    return {name: metrics(group) for name, group in sorted(buckets.items())}


def _stratum(outcome: Outcome, key: str) -> str:
    if key == "language":
        return outcome.language
    if key == "coordinates":
        return "proximity" if outcome.proximity_available else "missing"
    if key == "category":
        return outcome.category_relation
    raise ValueError(f"unknown stratum {key!r}")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def build_report() -> dict[str, Any]:
    agent = DuplicateDetectionAgent(use_llm=False)

    curated_pairs = load_labelled_pairs()
    curated_outcomes = evaluate(agent, curated_pairs)

    synthetic_pairs = load_synthetic_pairs()
    synthetic_outcomes = evaluate(agent, synthetic_pairs) if synthetic_pairs else []

    report: dict[str, Any] = {
        "curated": {
            "metrics": metrics(curated_outcomes),
            "by_language": stratified(curated_outcomes, "language"),
            "by_coordinates": stratified(curated_outcomes, "coordinates"),
            "by_category": stratified(curated_outcomes, "category"),
            "outcomes": [asdict(outcome) for outcome in curated_outcomes],
        }
    }
    if synthetic_outcomes:
        report["planted_synthetic"] = {
            "metrics": metrics(synthetic_outcomes),
            "by_language": stratified(synthetic_outcomes, "language"),
            "by_coordinates": stratified(synthetic_outcomes, "coordinates"),
        }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Offline duplicate-detection benchmark",
        "",
        "TF-IDF cosine (char/word n-grams) + Haversine proximity, `use_llm=False`.",
        "Weights: text 0.70 / geography 0.30, with a category-mismatch guard (x0.85).",
        "Generated by `ml/duplicate_detection/evaluate.py`.",
        "",
    ]

    curated = report["curated"]
    lines.append("## Hand-reviewed pairs")
    lines.append("")
    lines.append(_metrics_markdown(curated["metrics"]))
    lines.append("")
    for key, title in (("by_language", "language"), ("by_coordinates", "coordinates"),
                       ("by_category", "category")):
        lines.append(f"### By {title}")
        lines.append("")
        lines.append("| " + title + " | pairs | precision | recall | f1 | accuracy |")
        lines.append("|---|---|---|---|---|---|")
        for name, values in curated[key].items():
            lines.append(
                f"| {name} | {values['pairs']} | {values['precision']} | "
                f"{values['recall']} | {values['f1']} | {values['accuracy']} |"
            )
        lines.append("")

    lines.append("### Per-pair decisions")
    lines.append("")
    lines.append("| pair | label | predicted | score | text | geo | distance (m) | ok |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for outcome in curated["outcomes"]:
        distance = "-" if outcome["distance_m"] is None else f"{outcome['distance_m']:.0f}"
        geo = "-" if outcome["geographic_score"] is None else f"{outcome['geographic_score']:.2f}"
        ok = "yes" if outcome["predicted"] == (outcome["label"] == POSITIVE) else "NO"
        lines.append(
            f"| {outcome['pair_id']} | {outcome['label']} | {outcome['predicted']} | "
            f"{outcome['match_score']:.2f} | {outcome['text_similarity']:.2f} | {geo} | "
            f"{distance} | {ok} |"
        )
    lines.append("")

    if "planted_synthetic" in report:
        planted = report["planted_synthetic"]
        lines.append("## Planted duplicates in the synthetic dataset")
        lines.append("")
        lines.append(_metrics_markdown(planted["metrics"]))
        lines.append("")
        lines.append("| language | pairs | precision | recall | f1 | accuracy |")
        lines.append("|---|---|---|---|---|---|")
        for name, values in planted["by_language"].items():
            lines.append(
                f"| {name} | {values['pairs']} | {values['precision']} | "
                f"{values['recall']} | {values['f1']} | {values['accuracy']} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def _metrics_markdown(values: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- Pairs: **{values['pairs']}** "
            f"({values['positives']} duplicate / {values['negatives']} distinct)",
            f"- Precision: **{values['precision']}** | Recall: **{values['recall']}** | "
            f"F1: **{values['f1']}** | Accuracy: **{values['accuracy']}**",
            f"- Confusion: TP {values['true_positive']} / FP {values['false_positive']} / "
            f"FN {values['false_negative']} / TN {values['true_negative']}",
            f"- Pairs flagged for human review: {values['flagged_for_review']} "
            "(the agent never merges, links or rejects)",
        ]
    )


def _print_summary(report: dict[str, Any]) -> None:
    curated = report["curated"]["metrics"]
    print("Hand-reviewed pairs")
    print(
        f"  pairs={curated['pairs']} precision={curated['precision']} "
        f"recall={curated['recall']} f1={curated['f1']} accuracy={curated['accuracy']}"
    )
    print(
        f"  TP={curated['true_positive']} FP={curated['false_positive']} "
        f"FN={curated['false_negative']} TN={curated['true_negative']}"
    )
    mismatches = [
        outcome
        for outcome in report["curated"]["outcomes"]
        if outcome["predicted"] != (outcome["label"] == POSITIVE)
    ]
    for outcome in mismatches:
        print(
            f"  ! {outcome['pair_id']}: label={outcome['label']} "
            f"predicted={outcome['predicted']} score={outcome['match_score']}"
        )

    if "planted_synthetic" in report:
        planted = report["planted_synthetic"]["metrics"]
        print("\nPlanted duplicates in the synthetic dataset")
        print(
            f"  pairs={planted['pairs']} precision={planted['precision']} "
            f"recall={planted['recall']} f1={planted['f1']} accuracy={planted['accuracy']}"
        )
        print(
            f"  TP={planted['true_positive']} FP={planted['false_positive']} "
            f"FN={planted['false_negative']} TN={planted['true_negative']}"
        )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report = build_report()
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    _print_summary(report)
    print(f"\nWrote {REPORT_JSON.name} and {REPORT_MD.name} to {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
