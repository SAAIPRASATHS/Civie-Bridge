"""WS-1 Tamil correctness baseline evaluation harness.

Drives the **real** intake and classification agents over the Tamil evaluation
dataset in ``ws1_tamil_cases.json`` and records, for every case, the fields the
WS-1 acceptance targets are defined over:

* detected language (and the evidence behind it);
* extracted location (``location_text`` / ``ward``);
* English normalisation of the description;
* the classification category, priority and department *recommendation*;
* latency, ``llm_used`` and ``degraded``.

This is an evaluation harness, not application code. It imports the agents
read-only and writes nothing except its JSON report, so it can be run against a
frozen baseline without changing production behaviour.

Run from the ``backend`` directory::

    python scripts/ws1_tamil_eval.py --no-llm      # deterministic baseline
    python scripts/ws1_tamil_eval.py               # with the local model
    python scripts/ws1_tamil_eval.py --group romanised_tamil --limit 2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.classification import ClassificationAgent  # noqa: E402
from app.agents.intake import IntakeAgent  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "ws1_tamil_cases.json"
DEFAULT_REPORT = HERE / "ws1_tamil_eval_report.json"
REPO_ROOT = BACKEND_DIR.parent

TAMIL_START, TAMIL_END = 0x0B80, 0x0BFF


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _has_tamil_script(text: str | None) -> bool:
    return any(TAMIL_START <= ord(ch) <= TAMIL_END for ch in (text or ""))


def _git_rev() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # pragma: no cover - git may be unavailable
        return None


# ---------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------


def run_case(case: dict[str, Any], *, use_llm: bool) -> dict[str, Any]:
    """Analyse one case and return the record the metrics are computed from."""
    intake_agent = IntakeAgent(use_llm=use_llm)
    classification_agent = ClassificationAgent(use_llm=use_llm)

    started = time.perf_counter()
    intake = intake_agent.run(case["text"])
    classification = classification_agent.run(intake)
    wall_ms = round((time.perf_counter() - started) * 1000, 1)

    record: dict[str, Any] = {
        "id": case["id"],
        "group": case["group"],
        "text": case["text"],
        # -- intake --------------------------------------------------------
        "language": intake.language.value,
        "language_confidence": intake.language_confidence,
        "language_reason": intake.language_reason,
        "normalized_description": intake.normalized_description,
        "problem_type": intake.problem_type,
        "duration_days": intake.duration_days,
        "location_text": intake.location_text,
        "ward": intake.ward,
        "landmarks": intake.landmarks,
        "missing_information": intake.missing_information,
        "needs_clarification": intake.needs_clarification,
        "intake_llm_used": intake.llm_used,
        "intake_degraded": intake.degraded,
        "intake_latency_ms": intake.latency_ms,
        # -- classification ------------------------------------------------
        "category": classification.category.value if classification.category else None,
        "priority": classification.priority.value,
        "urgency_score": classification.urgency_score,
        "confidence": classification.confidence,
        "needs_human_review": classification.needs_human_review,
        "recommended_department_code": classification.department_code,
        "classification_llm_used": classification.llm_used,
        "classification_degraded": classification.degraded,
        "classification_latency_ms": classification.latency_ms,
        # -- run metadata --------------------------------------------------
        "pipeline_wall_ms": wall_ms,
        "llm_used": bool(intake.llm_used or classification.llm_used),
        "model_name": intake.model_name or classification.model_name,
    }
    record["normalisation_is_english"] = _normalisation_ok(record)
    return record


# ---------------------------------------------------------------------------
# Per-target checks
# ---------------------------------------------------------------------------


def _category_ok(case: dict[str, Any], record: dict[str, Any]) -> bool:
    if case.get("expect_category") is not None:
        return record["category"] == case["expect_category"]
    expected_any = case.get("expect_category_any")
    if expected_any:
        return record["category"] in expected_any
    return True


def _location_ok(case: dict[str, Any], record: dict[str, Any]) -> bool:
    """WS-1 target: the location must be correct, or honestly null.

    A *wrong* non-null location is the failure this guards against.
    """
    expected = case.get("expect_location")
    actual = record["location_text"]
    if expected is None:
        return actual is None
    return actual == expected or actual is None


def _normalisation_ok(record: dict[str, Any]) -> bool:
    """WS-1 target: the description must actually be restated in English.

    A deterministic fallback is *not* a successful normalisation. Those cases are
    counted and reported separately so a fallback can never inflate this metric.
    """
    if record["intake_degraded"] or not record["intake_llm_used"]:
        return False
    normalized = (record["normalized_description"] or "").strip()
    if not normalized or _has_tamil_script(normalized):
        return False
    # A romanised-Tamil source carries no Tamil script to detect, so require that
    # the model actually rewrote the text instead of echoing the original back.
    if record["language"] != "en" and normalized.casefold() == record["text"].strip().casefold():
        return False
    return True


def _metric(cases: list[dict[str, Any]], passed: int, target: float) -> dict[str, Any]:
    total = len(cases)
    ratio = round(passed / total, 3) if total else 0.0
    return {
        "passed": passed,
        "total": total,
        "ratio": ratio,
        "target": target,
        "meets_target": total > 0 and ratio >= target,
    }


def compute_metrics(
    cases: list[dict[str, Any]], records: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {record["id"]: record for record in records}

    trans_cases = [c for c in cases if c.get("check_transliteration_classification")]
    loc_cases = [c for c in cases if c.get("check_location")]
    norm_cases = [c for c in cases if c.get("check_normalisation")]

    trans_pass = sum(1 for c in trans_cases if _category_ok(c, by_id[c["id"]]))
    loc_pass = sum(1 for c in loc_cases if _location_ok(c, by_id[c["id"]]))
    # Strict sub-count for reporting: how many locations were actually right.
    loc_strict = sum(
        1
        for c in loc_cases
        if by_id[c["id"]]["location_text"] is not None
        and by_id[c["id"]]["location_text"] == c.get("expect_location")
    )
    norm_pass = sum(1 for c in norm_cases if _normalisation_ok(by_id[c["id"]]))

    lang_cases = [c for c in cases if c.get("expect_language")]
    lang_pass = sum(1 for c in lang_cases if by_id[c["id"]]["language"] == c["expect_language"])
    cat_cases = [c for c in cases if c.get("expect_category") or c.get("expect_category_any")]
    cat_pass = sum(1 for c in cat_cases if _category_ok(c, by_id[c["id"]]))

    return {
        "transliteration_classification": _metric(trans_cases, trans_pass, 0.8),
        "location_extraction": {
            **_metric(loc_cases, loc_pass, 0.9),
            "strictly_correct_non_null": loc_strict,
        },
        "english_normalisation": _metric(norm_cases, norm_pass, 0.9),
        "informational": {
            "language_detection": _metric(lang_cases, lang_pass, 1.0),
            "category_accuracy": _metric(cat_cases, cat_pass, 1.0),
            "llm_coverage": _metric(
                records, sum(1 for record in records if record["llm_used"]), 1.0
            ),
        },
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_table(records: list[dict[str, Any]]) -> None:
    header = (
        f"{'id':<14}{'group':<20}{'lang':<8}{'category':<18}"
        f"{'loc':<24}{'norm':<5}{'llm':<4}{'ms':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in records:
        location = (r["location_text"] or "-")[:22]
        print(
            f"{r['id']:<14}{r['group']:<20}{r['language']:<8}"
            f"{(r['category'] or '-'):<18}{location:<24}"
            f"{('Y' if r['normalisation_is_english'] else 'n'):<5}"
            f"{('Y' if r['llm_used'] else 'n'):<4}{r['pipeline_wall_ms']:>8.1f}"
        )


def _print_metrics(metrics: dict[str, Any]) -> None:
    print()
    for name in (
        "transliteration_classification",
        "location_extraction",
        "english_normalisation",
    ):
        m = metrics[name]
        verdict = "PASS" if m["meets_target"] else "FAIL"
        extra = (
            f" (strictly correct non-null: {m['strictly_correct_non_null']})"
            if "strictly_correct_non_null" in m
            else ""
        )
        print(
            f"{verdict}  {name}: {m['passed']}/{m['total']} "
            f"= {m['ratio']:.2f} (target >= {m['target']:.2f}){extra}"
        )
    print()
    for name, m in metrics["informational"].items():
        print(f"info  {name}: {m['passed']}/{m['total']} = {m['ratio']:.2f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WS-1 Tamil correctness baseline evaluation")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Run the deterministic agents only (no model calls).",
    )
    parser.add_argument("--group", default=None, help="Only run cases in this group.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases.")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - older consoles
        pass

    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = dataset["cases"]
    if args.group:
        cases = [c for c in cases if c["group"] == args.group]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        print("No cases selected.")
        return 1

    use_llm = not args.no_llm
    started = time.perf_counter()
    records: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['id']} ...", flush=True)
        records.append(run_case(case, use_llm=use_llm))
    total_wall_s = round(time.perf_counter() - started, 1)

    metrics = compute_metrics(cases, records)
    _print_table(records)
    _print_metrics(metrics)

    report = {
        "harness": "ws1_tamil_eval",
        "git_rev": _git_rev(),
        "use_llm": use_llm,
        "case_count": len(cases),
        "total_wall_seconds": total_wall_s,
        "acceptance_targets": dataset.get("acceptance_targets", {}),
        "metrics": metrics,
        "cases": cases,
        "records": records,
        "deterministic_fallback_case_ids": [
            record["id"] for record in records if not record["llm_used"]
        ],
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nReport written to {args.report}")
    return 0 if all(
        metrics[name]["meets_target"]
        for name in (
            "transliteration_classification",
            "location_extraction",
            "english_normalisation",
        )
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
