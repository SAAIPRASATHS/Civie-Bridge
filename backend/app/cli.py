"""CivicBridge AI command line interface.

Phase 1 exposes the environment verification command. Phase 2 adds the dataset
commands:

    cd backend
    python -m app.cli health
    python -m app.cli generate-dataset
    python -m app.cli seed-db
    python -m app.cli dataset-report

Every check is a real probe against the local infrastructure. Failures are
reported with remediation hints and produce a non-zero exit code so the
command is safe to use in CI or a pre-flight script.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import REPO_ROOT, settings
from app.core.logging import configure_logging
from app.database.session import check_database, session_scope
from app.datasets import (
    DEFAULT_COUNT,
    DEFAULT_DUPLICATE_COUNT,
    DEFAULT_SEED,
    build_report,
    generate_complaints,
    summarise,
    write_dataset_files,
    write_report_files,
)
from app.datasets.seed import seed_database
from app.services.ollama_service import (
    OllamaUnavailableError,
    check_ollama_service,
    find_model,
    resolve_chat_ollama_class,
    run_llm_smoke_test,
)

#: Where dataset and report artefacts are written.
DATASET_OUTPUT_DIR = REPO_ROOT / "datasets"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

DEFAULT_SMOKE_PROMPT = (
    "You are CivicBridge AI, a government grievance understanding assistant.\n\n"
    "Analyze this complaint:\n\n"
    '"Garbage has not been collected near our street for the past five days."\n\n'
    "Return exactly these five labelled lines and nothing else:\n"
    "1. Complaint category\n"
    "2. Responsible department\n"
    "3. Priority\n"
    "4. Location information required\n"
    "5. Recommended next action"
)


@dataclass
class CheckResult:
    """A single named verification step."""

    name: str
    status: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.status != FAIL


def _print_header() -> None:
    print()
    print("=" * 78)
    print(f" {settings.app_name} - Environment Verification")
    print(f" environment={settings.environment}  version={settings.app_version}")
    print("=" * 78)


def _print_results(results: list[CheckResult], verbose: bool) -> None:
    width = max(len(r.name) for r in results) + 2
    print()
    for result in results:
        print(f"  [{result.status}] {result.name:<{width}}{result.detail}")
        if verbose:
            print(f"          -> {result.detail}")
    print()

    failed = [r for r in results if r.status == FAIL]
    skipped = [r for r in results if r.status == SKIP]
    print("-" * 78)
    print(
        f"  {len(results) - len(failed) - len(skipped)} passed | "
        f"{len(failed)} failed | {len(skipped)} skipped"
    )
    if failed:
        print("\n  Blocking issues:")
        for result in failed:
            print(f"   - {result.name}: {result.detail}")
    print("-" * 78)
    print()


def _check_llm_response(results: list[CheckResult], prompt: str, run_llm: bool) -> None:
    if not run_llm:
        results.append(CheckResult("LLM response test", SKIP, "disabled with --no-llm"))
        return

    try:
        response = run_llm_smoke_test(prompt)
    except OllamaUnavailableError as exc:
        results.append(CheckResult("LLM response test", FAIL, str(exc)))
        return

    preview = response.text.replace("\n", " | ")
    if len(preview) > 400:
        preview = preview[:400] + "..."
    results.append(
        CheckResult(
            "LLM response test",
            PASS,
            f"{response.latency_ms:.0f} ms via {response.provider} "
            f"({response.model})\n{' ' * 12}model output: {preview}",
        )
    )


def run_health_checks(
    prompt: str = DEFAULT_SMOKE_PROMPT, run_llm: bool = True
) -> list[CheckResult]:
    """Execute every Phase 1 verification step in dependency order."""
    results: list[CheckResult] = []

    # 1. PostgreSQL connectivity -------------------------------------------------
    db = check_database()
    if db.connected:
        results.append(
            CheckResult(
                "PostgreSQL connection",
                PASS,
                f"server {db.server_version} @ {settings.postgres_host}:{settings.postgres_port}",
            )
        )
        results.append(
            CheckResult(
                "CivicBridge database",
                PASS,
                f"{db.database_name} (owner {db.database_owner}, user {db.current_user})",
            )
        )
        extensions = db.extension_summary
        detail = f"extensions: {extensions}"
        if db.missing_expected_extensions:
            detail += f" | missing: {', '.join(db.missing_expected_extensions)}"
        results.append(CheckResult("Database extensions", PASS, detail))
    else:
        results.append(
            CheckResult(
                "PostgreSQL connection",
                FAIL,
                f"{db.error} | url={db.database_url}",
            )
        )
        results.append(
            CheckResult(
                "CivicBridge database",
                FAIL,
                "unavailable - PostgreSQL connection failed first",
            )
        )
        results.append(CheckResult("Database extensions", SKIP, "not checked without a connection"))

    # 2. Ollama service ---------------------------------------------------------
    ollama = check_ollama_service()
    if ollama.reachable:
        results.append(
            CheckResult(
                "Ollama service",
                PASS,
                f"v{ollama.version} @ {ollama.base_url} ({len(ollama.models)} model(s) pulled)",
            )
        )
    else:
        results.append(CheckResult("Ollama service", FAIL, ollama.error or "unreachable"))

    # 3. Configured model -------------------------------------------------------
    if not ollama.reachable:
        results.append(
            CheckResult(
                f"Model {settings.ollama_model}",
                SKIP,
                "not verified - Ollama service is not reachable",
            )
        )
    else:
        model = find_model(ollama, settings.ollama_model)
        if model is None:
            results.append(
                CheckResult(
                    f"Model {settings.ollama_model}",
                    FAIL,
                    "not pulled | run: ollama pull "
                    f"{settings.ollama_model} | available: {', '.join(ollama.model_names) or 'none'}",
                )
            )
        else:
            caps = ", ".join(model.capabilities) or "completion"
            results.append(
                CheckResult(
                    f"Model {model.name}",
                    PASS,
                    f"{model.parameter_size} {model.quantization_level} "
                    f"{model.size_human} | capabilities: {caps}",
                )
            )

    # 4. LangChain integration --------------------------------------------------
    try:
        provider, _ = resolve_chat_ollama_class()
    except OllamaUnavailableError as exc:
        results.append(
            CheckResult(
                "LangChain Ollama",
                FAIL,
                f"{exc} | run: python -m pip install langchain-ollama",
            )
        )
    else:
        results.append(CheckResult("LangChain Ollama", PASS, f"provider: {provider}"))

    # 5. Live inference ---------------------------------------------------------
    if not ollama.reachable:
        results.append(
            CheckResult("LLM response test", SKIP, "not run - Ollama service is unreachable")
        )
    elif find_model(ollama, settings.ollama_model) is None:
        results.append(
            CheckResult(
                "LLM response test",
                SKIP,
                f"not run - model {settings.ollama_model!r} is not available",
            )
        )
    else:
        _check_llm_response(results, prompt, run_llm)

    return results


def _cmd_health(args: argparse.Namespace) -> int:
    _print_header()
    print(
        f"\n  config: model={settings.ollama_model}  base_url={settings.ollama_base_url}"
        f"\n  config: database={settings.safe_database_url}"
    )
    results = run_health_checks(prompt=args.prompt, run_llm=not args.no_llm)

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        verbose = bool(args.verbose or any(r.status == FAIL for r in results))
        _print_results(results, verbose=verbose)

    return 0 if all(r.passed for r in results) else 1


def _print_distribution(label: str, mapping: dict[str, int], total: int) -> None:
    print(f"  {label}:")
    for key, value in sorted(mapping.items(), key=lambda item: -item[1]):
        share = value / total * 100 if total else 0
        print(f"    {key:<22} {value:>6}  ({share:.1f}%)")


def _cmd_generate_dataset(args: argparse.Namespace) -> int:
    complaints = generate_complaints(
        count=args.count, duplicate_count=args.duplicates, seed=args.seed
    )
    summary = summarise(complaints)
    paths = write_dataset_files(complaints, args.output_dir)

    print()
    print(f"Generated {summary.total} complaints (seed={args.seed})")
    print(f"  primary={summary.primary}  duplicates={summary.duplicates}")
    _print_distribution("category", summary.by_category, summary.total)
    _print_distribution("language", summary.by_language, summary.total)
    _print_distribution("priority", summary.by_priority, summary.total)
    _print_distribution("status", summary.by_status, summary.total)
    print(f"  missing coordinates: {summary.missing_coordinates}")
    print()
    for kind, path in paths.items():
        print(f"  wrote {kind}: {path}")
    print()
    return 0


def _cmd_seed_db(args: argparse.Namespace) -> int:
    print()
    print(f"Seeding {settings.safe_database_url} (seed={args.seed}, count={args.count})")
    with session_scope() as session:
        report, complaints = seed_database(
            session,
            count=args.count,
            duplicate_count=args.duplicates,
            seed=args.seed,
            reset=not args.keep_existing,
        )
        write_dataset_files(complaints, args.output_dir)
        dataset_report = build_report(session)
        paths = write_report_files(dataset_report, args.output_dir)

    print()
    for key, value in report.as_dict().items():
        print(f"  {key:<22} {value}")
    print()
    passed = sum(1 for check in dataset_report.checks if check.passed)
    print(f"  validation: {passed}/{len(dataset_report.checks)} checks passed")
    for check in dataset_report.checks:
        if not check.passed:
            print(f"   - FAIL {check.name}: {check.detail}")
    print()
    for kind, path in paths.items():
        print(f"  wrote report {kind}: {path}")
    print()
    return 0 if dataset_report.all_checks_passed else 1


def _cmd_dataset_report(args: argparse.Namespace) -> int:
    with session_scope() as session:
        dataset_report = build_report(session)
        paths = write_report_files(dataset_report, args.output_dir)

    if args.json:
        print(json.dumps(dataset_report.as_dict(), indent=2, default=str))
    else:
        print()
        print(
            f"Database: {dataset_report.database['name']} "
            f"(revision {dataset_report.migration_revision})"
        )
        print()
        print("  Row counts:")
        for table, count in dataset_report.table_counts.items():
            print(f"    {table:<26} {count:>7}")
        print()
        complaints = dataset_report.complaints
        print(f"  Complaints: {complaints['total']}")
        _print_distribution("category", complaints["by_category"], complaints["total"])
        _print_distribution("language", complaints["by_language"], complaints["total"])
        print()
        print("  Validation:")
        for check in dataset_report.checks:
            status = PASS if check.passed else FAIL
            print(f"    [{status}] {check.name}: {check.detail}")
        passed = sum(1 for check in dataset_report.checks if check.passed)
        print()
        print(f"  {passed}/{len(dataset_report.checks)} checks passed")
        print()
        for kind, path in paths.items():
            print(f"  wrote report {kind}: {path}")
        print()

    return 0 if dataset_report.all_checks_passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="CivicBridge AI operational commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser(
        "health", help="Verify PostgreSQL, Ollama, the configured model and live inference."
    )
    health.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the live LLM inference test (infrastructure checks only).",
    )
    health.add_argument(
        "--prompt",
        default=DEFAULT_SMOKE_PROMPT,
        help="Prompt used for the live LLM inference test.",
    )
    health.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    health.add_argument(
        "--verbose", action="store_true", help="Always print full detail for every check."
    )
    health.set_defaults(func=_cmd_health)

    def add_dataset_arguments(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--count",
            type=int,
            default=DEFAULT_COUNT,
            help=f"Number of primary complaints to generate (default {DEFAULT_COUNT}).",
        )
        sub.add_argument(
            "--duplicates",
            type=int,
            default=DEFAULT_DUPLICATE_COUNT,
            help=f"Number of planted duplicates (default {DEFAULT_DUPLICATE_COUNT}).",
        )
        sub.add_argument(
            "--seed",
            type=int,
            default=DEFAULT_SEED,
            help=f"Deterministic RNG seed (default {DEFAULT_SEED}).",
        )

    generate = subparsers.add_parser(
        "generate-dataset",
        help="Generate the synthetic complaint dataset files without touching the database.",
    )
    add_dataset_arguments(generate)
    generate.add_argument(
        "--output-dir",
        type=Path,
        default=DATASET_OUTPUT_DIR,
        help=f"Directory for the CSV/JSON artefacts (default {DATASET_OUTPUT_DIR}).",
    )
    generate.set_defaults(func=_cmd_generate_dataset)

    seed = subparsers.add_parser(
        "seed-db",
        help="Load the prototype reference data and the synthetic dataset into PostgreSQL.",
    )
    add_dataset_arguments(seed)
    seed.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not truncate the CivicBridge tables before seeding.",
    )
    seed.add_argument(
        "--output-dir",
        type=Path,
        default=DATASET_OUTPUT_DIR,
        help="Directory where the dataset and report artefacts are written.",
    )
    seed.set_defaults(func=_cmd_seed_db)

    report = subparsers.add_parser(
        "dataset-report",
        help="Validate the stored dataset and write the Phase 2 report.",
    )
    report.add_argument(
        "--output-dir",
        type=Path,
        default=DATASET_OUTPUT_DIR,
        help=f"Directory for the report artefacts (default {DATASET_OUTPUT_DIR}).",
    )
    report.add_argument("--json", action="store_true", help="Emit the report as JSON on stdout.")
    report.set_defaults(func=_cmd_dataset_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
