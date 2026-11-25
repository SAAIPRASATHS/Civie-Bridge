"""Complaint Intake Agent - standalone entry point (Phase 3).

The implementation lives in ``backend/app/agents/intake.py`` so the FastAPI app
and the (Phase 6) LangGraph workflow share exactly one copy of the agent. This
module is the runnable wrapper named by the Phase 3 specification.

Usage (from the repository root):

    python agents/intake_agent.py "Garbage has not been collected near our street for five days."
    python agents/intake_agent.py --file complaint.txt
    python agents/intake_agent.py --no-llm "..."          # deterministic only
    python agents/intake_agent.py --json "..."

Exit codes: 0 = analysed, 1 = invalid input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agents.intake import IntakeAgent
from app.core.logging import configure_logging
from app.schemas.agent import IntakeResult


def analyze(text: str, *, use_llm: bool = True) -> IntakeResult:
    """Analyse one complaint and return the validated intake result."""
    return IntakeAgent(use_llm=use_llm).run(text)


def _print_result(result: IntakeResult) -> None:
    print("--- intake ------------------------------------------------------------")
    rows = [
        ("language", f"{result.language.value} ({result.language_confidence:.2f})"),
        ("normalized", result.normalized_description),
        ("problem_type", result.problem_type or "-"),
        ("duration_days", "-" if result.duration_days is None else f"{result.duration_days:g}"),
        ("location", result.location_text or "-"),
        ("ward", result.ward or "-"),
        ("landmarks", ", ".join(result.landmarks) or "-"),
        ("missing", ", ".join(result.missing_information) or "-"),
        ("confidence", f"{result.confidence:.2f}"),
        ("llm_used", str(result.llm_used)),
        ("degraded", str(result.degraded)),
        ("reasoning", result.reasoning),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value}")
    for question in result.clarification_questions:
        print(f"  question{' ' * (width - 8)}  {question}")
    print("-----------------------------------------------------------------------")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python agents/intake_agent.py",
        description="Run the CivicBridge AI Complaint Intake Agent on one complaint.",
    )
    parser.add_argument(
        "text", nargs="?", help="Complaint text (English, Tamil or transliterated)."
    )
    parser.add_argument("--file", type=Path, help="Read the complaint from a UTF-8 text file.")
    parser.add_argument("--no-llm", action="store_true", help="Deterministic extraction only.")
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON.")
    args = parser.parse_args(argv)

    configure_logging()

    text = args.file.read_text(encoding="utf-8") if args.file else (args.text or "")
    if not text.strip():
        parser.error("provide the complaint as a positional argument or with --file")

    result = analyze(text, use_llm=not args.no_llm)
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
