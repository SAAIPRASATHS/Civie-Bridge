"""Classification Agent - standalone entry point (Phase 3).

The implementation lives in ``backend/app/agents/classification.py``; this is
the runnable wrapper named by the Phase 3 specification.

Because a classifier needs the intake result to work from, the standalone
wrapper runs the full two-agent pipeline (intake -> classification) unless
``--from-json`` is used to feed a previously produced :class:`IntakeResult`.

Usage (from the repository root):

    python agents/classification_agent.py "For five days garbage has not been collected."
    python agents/classification_agent.py --from-json intake.json
    python agents/classification_agent.py --no-llm "..."
    python agents/classification_agent.py --json "..."

Exit codes: 0 = classified, 1 = invalid input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agents.classification import ClassificationAgent
from app.agents.intake import IntakeAgent
from app.core.logging import configure_logging
from app.schemas.agent import ClassificationResult, IntakeResult


def classify(source: IntakeResult | str, *, use_llm: bool = True) -> ClassificationResult:
    """Classify an intake result, or raw text (in which case intake runs first)."""
    if isinstance(source, str):
        source = IntakeAgent(use_llm=use_llm).run(source)
    return ClassificationAgent(use_llm=use_llm).run(source)


def _print_result(result: ClassificationResult) -> None:
    print("--- classification ----------------------------------------------------")
    rows = [
        ("category", result.category.value if result.category else "undetermined"),
        ("subcategory", result.subcategory or "-"),
        ("priority", result.priority.value),
        ("urgency_score", f"{result.urgency_score:.2f}"),
        ("deterministic", result.deterministic_priority.value),
        ("model_priority", result.model_priority.value if result.model_priority else "-"),
        ("escalation", result.escalation_reason or "-"),
        ("confidence", f"{result.confidence:.2f}"),
        (
            "department",
            f"{result.department_code} - {result.department_name}"
            if result.department_code
            else "-",
        ),
        ("guideline", result.guideline_code or "-"),
        ("sla_hours", "-" if result.sla_hours is None else str(result.sla_hours)),
        ("matched_signals", ", ".join(result.matched_signals) or "-"),
        ("needs_human_review", str(result.needs_human_review)),
        ("review_reason", result.review_reason or "-"),
        ("llm_used", str(result.llm_used)),
        ("degraded", str(result.degraded)),
        ("next_action", result.recommended_next_action),
        ("reasoning", result.reasoning),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value}")
    print("-----------------------------------------------------------------------")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python agents/classification_agent.py",
        description="Run the CivicBridge AI Classification Agent on one complaint.",
    )
    parser.add_argument(
        "text", nargs="?", help="Complaint text (English, Tamil or transliterated)."
    )
    parser.add_argument("--file", type=Path, help="Read the complaint from a UTF-8 text file.")
    parser.add_argument(
        "--from-json",
        type=Path,
        help="Classify a previously produced intake result (JSON file) instead of raw text.",
    )
    parser.add_argument("--no-llm", action="store_true", help="Deterministic keywords only.")
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON.")
    args = parser.parse_args(argv)

    configure_logging()

    if args.from_json:
        payload = json.loads(args.from_json.read_text(encoding="utf-8"))
        source: IntakeResult | str = IntakeResult.model_validate(payload)
    else:
        text = args.file.read_text(encoding="utf-8") if args.file else (args.text or "")
        if not text.strip():
            parser.error("provide the complaint, --file or --from-json")
        source = text

    result = classify(source, use_llm=not args.no_llm)
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
