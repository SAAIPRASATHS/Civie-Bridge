"""Dataset and database validation reporting.

The report is generated from the *live database*, never from the generator's
in-memory state, so a passing report means the rows actually stored satisfy
every Phase 2 invariant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.datasets.reference_data import DEPARTMENT_PROVENANCE, GUIDELINE_PROVENANCE
from app.datasets.seed import database_revision, table_row_counts
from app.models import (
    TABLE_NAMES,
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    ComplaintStatus,
    enum_values,
)

#: Dataset composition is validated over the synthetic corpus only, so a single
#: in-flight citizen submission (which may legitimately have no category yet)
#: cannot fail a dataset check. Foreign-key integrity checks stay whole-database,
#: because a broken reference anywhere is a real defect.
DATASET_SCOPE = "is_synthetic IS TRUE"


@dataclass
class ValidationCheck:
    """One named assertion about the stored data."""

    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class DatasetReport:
    """Everything the Phase 2 report needs, computed from the database."""

    generated_at: str
    database: dict[str, Any]
    migration_revision: str | None
    table_counts: dict[str, int]
    complaints: dict[str, Any]
    duplicates: dict[str, Any]
    coordinates: dict[str, Any]
    reference_data: dict[str, Any]
    checks: list[ValidationCheck] = field(default_factory=list)

    @property
    def all_checks_passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "database": self.database,
            "migration_revision": self.migration_revision,
            "table_counts": self.table_counts,
            "complaints": self.complaints,
            "duplicates": self.duplicates,
            "coordinates": self.coordinates,
            "reference_data": self.reference_data,
            "checks": [check.as_dict() for check in self.checks],
            "all_checks_passed": self.all_checks_passed,
        }


def _scalar(session: Session, sql: str) -> Any:
    return session.execute(text(sql)).scalar_one()


def _rows(session: Session, sql: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in session.execute(text(sql)).all()]


def _group_counts(session: Session, column: str) -> dict[str, int]:
    rows = _rows(
        session,
        f"SELECT {column}::text, count(*) FROM complaints "
        f"WHERE {DATASET_SCOPE} GROUP BY {column} ORDER BY 2 DESC",
    )
    return {str(value): int(count) for value, count in rows}


def _collect_checks(session: Session, counts: dict[str, int]) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []

    missing_tables = [name for name in TABLE_NAMES if name not in counts]
    checks.append(
        ValidationCheck(
            "All 10 tables exist in PostgreSQL",
            not missing_tables,
            f"{len(TABLE_NAMES) - len(missing_tables)}/{len(TABLE_NAMES)} present"
            + (f" | missing: {missing_tables}" if missing_tables else ""),
        )
    )

    revision = database_revision(session)
    checks.append(
        ValidationCheck(
            "Alembic migration applied",
            revision is not None,
            f"revision={revision}",
        )
    )

    dataset_total = _scalar(session, f"SELECT count(*) FROM complaints WHERE {DATASET_SCOPE}")
    checks.append(
        ValidationCheck(
            "Dataset size >= 1000 complaints",
            dataset_total >= 1000,
            f"complaints={dataset_total}",
        )
    )

    categories = {key for key, value in _group_counts(session, "category").items() if value}
    expected_categories = set(enum_values(ComplaintCategory))
    checks.append(
        ValidationCheck(
            "All 4 categories present",
            categories == expected_categories,
            "categories=" + ", ".join(sorted(categories)),
        )
    )

    languages = {key for key, value in _group_counts(session, "language").items() if value}
    expected_languages = set(enum_values(ComplaintLanguage))
    checks.append(
        ValidationCheck(
            "All 3 languages present (en, ta, ta-latn)",
            languages == expected_languages,
            "languages=" + ", ".join(sorted(languages)),
        )
    )

    duplicate_rows = _scalar(
        session,
        f"SELECT count(*) FROM complaints WHERE duplicate_of_id IS NOT NULL "
        f"AND {DATASET_SCOPE}",
    )
    orphan_duplicates = _scalar(
        session,
        f"SELECT count(*) FROM complaints c WHERE c.duplicate_of_id IS NOT NULL "
        f"AND c.{DATASET_SCOPE} "
        "AND NOT EXISTS (SELECT 1 FROM complaints o WHERE o.id = c.duplicate_of_id)",
    )
    self_duplicates = _scalar(
        session,
        f"SELECT count(*) FROM complaints WHERE duplicate_of_id = id AND {DATASET_SCOPE}",
    )
    checks.append(
        ValidationCheck(
            "Planted duplicates present and resolvable",
            duplicate_rows > 0 and orphan_duplicates == 0 and self_duplicates == 0,
            f"duplicates={duplicate_rows} orphaned={orphan_duplicates} self_referential={self_duplicates}",
        )
    )

    missing_coordinates = _scalar(
        session,
        f"SELECT count(*) FROM complaints WHERE latitude IS NULL AND longitude IS NULL "
        f"AND {DATASET_SCOPE}",
    )
    half_coordinates = _scalar(
        session,
        f"SELECT count(*) FROM complaints WHERE (latitude IS NULL) <> (longitude IS NULL) "
        f"AND {DATASET_SCOPE}",
    )
    checks.append(
        ValidationCheck(
            "Missing coordinates handled (both columns NULL together)",
            missing_coordinates > 0 and half_coordinates == 0,
            f"without_location={missing_coordinates} half_pairs={half_coordinates}",
        )
    )

    invalid_coordinates = _scalar(
        session,
        "SELECT count(*) FROM complaints WHERE ((latitude IS NOT NULL AND "
        "(latitude < -90 OR latitude > 90)) OR (longitude IS NOT NULL AND "
        f"(longitude < -180 OR longitude > 180))) AND {DATASET_SCOPE}",
    )
    checks.append(
        ValidationCheck(
            "No invalid coordinates stored",
            invalid_coordinates == 0,
            f"out_of_range={invalid_coordinates}",
        )
    )

    valid_statuses = set(enum_values(ComplaintStatus))
    stored_statuses = set(_group_counts(session, "status"))
    checks.append(
        ValidationCheck(
            "Status values are all controlled vocabulary",
            stored_statuses <= valid_statuses,
            f"distinct_statuses={len(stored_statuses)}",
        )
    )

    valid_priorities = set(enum_values(ComplaintPriority))
    stored_priorities = set(_group_counts(session, "priority"))
    checks.append(
        ValidationCheck(
            "Priority values are all controlled vocabulary",
            stored_priorities <= valid_priorities,
            f"distinct_priorities={len(stored_priorities)}",
        )
    )

    fk_violations = 0
    fk_checks = {
        "complaints.citizen_id": "SELECT count(*) FROM complaints c WHERE c.citizen_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = c.citizen_id)",
        "complaints.current_department_id": "SELECT count(*) FROM complaints c WHERE "
        "c.current_department_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM departments d WHERE d.id = c.current_department_id)",
        "officers.department_id": "SELECT count(*) FROM officers o WHERE NOT EXISTS "
        "(SELECT 1 FROM departments d WHERE d.id = o.department_id)",
        "service_guidelines.department_id": "SELECT count(*) FROM service_guidelines g "
        "WHERE NOT EXISTS (SELECT 1 FROM departments d WHERE d.id = g.department_id)",
        "complaint_status_history.complaint_id": "SELECT count(*) FROM complaint_status_history h "
        "WHERE NOT EXISTS (SELECT 1 FROM complaints c WHERE c.id = h.complaint_id)",
    }
    for sql in fk_checks.values():
        violations = _scalar(session, sql)
        if violations:
            fk_violations += violations
    checks.append(
        ValidationCheck(
            "Foreign key relationships resolve",
            fk_violations == 0,
            f"checked={len(fk_checks)} relationships | violations={fk_violations}",
        )
    )

    prototype_departments = _scalar(
        session, "SELECT count(*) FROM departments WHERE is_prototype_data IS TRUE"
    )
    total_departments = counts.get("departments", 0)
    checks.append(
        ValidationCheck(
            "Every department is labelled prototype data",
            total_departments > 0 and prototype_departments == total_departments,
            f"prototype={prototype_departments}/{total_departments}",
        )
    )

    verified_guidelines = _scalar(
        session, "SELECT count(*) FROM service_guidelines WHERE is_verified IS TRUE"
    )
    total_guidelines = counts.get("service_guidelines", 0)
    checks.append(
        ValidationCheck(
            "No service guideline claims official verification",
            total_guidelines > 0 and verified_guidelines == 0,
            f"unverified={total_guidelines - verified_guidelines}/{total_guidelines}",
        )
    )

    return checks


def build_report(session: Session) -> DatasetReport:
    """Compute the full Phase 2 report from the live database."""
    counts = table_row_counts(session)

    by_category = _group_counts(session, "category")
    by_language = _group_counts(session, "language")
    by_priority = _group_counts(session, "priority")
    by_status = _group_counts(session, "status")

    total = _scalar(session, f"SELECT count(*) FROM complaints WHERE {DATASET_SCOPE}")
    duplicate_sample = _rows(
        session,
        "SELECT c.reference_id, o.reference_id, c.duplicate_similarity "
        "FROM complaints c JOIN complaints o ON o.id = c.duplicate_of_id "
        f"WHERE c.{DATASET_SCOPE} "
        "ORDER BY c.reference_id LIMIT 5",
    )
    missing_coordinates = _scalar(
        session,
        f"SELECT count(*) FROM complaints WHERE latitude IS NULL AND longitude IS NULL "
        f"AND {DATASET_SCOPE}",
    )

    report = DatasetReport(
        generated_at=datetime.now(UTC).isoformat(),
        database={
            "name": settings.postgres_db,
            "host": settings.postgres_host,
            "port": settings.postgres_port,
            "user": settings.postgres_user,
        },
        migration_revision=database_revision(session),
        table_counts=counts,
        complaints={
            "total": total,
            "by_category": by_category,
            "by_language": by_language,
            "by_priority": by_priority,
            "by_status": by_status,
        },
        duplicates={
            "total": _scalar(
                session,
                f"SELECT count(*) FROM complaints WHERE duplicate_of_id IS NOT NULL "
                f"AND {DATASET_SCOPE}",
            ),
            "similarity_min": _scalar(
                session,
                f"SELECT min(duplicate_similarity) FROM complaints WHERE "
                f"duplicate_of_id IS NOT NULL AND {DATASET_SCOPE}",
            ),
            "similarity_max": _scalar(
                session,
                f"SELECT max(duplicate_similarity) FROM complaints WHERE "
                f"duplicate_of_id IS NOT NULL AND {DATASET_SCOPE}",
            ),
            "examples": [
                {"duplicate": dup, "original": orig, "similarity": float(sim) if sim else None}
                for dup, orig, sim in duplicate_sample
            ],
        },
        coordinates={
            "with_coordinates": total - missing_coordinates,
            "missing": missing_coordinates,
            "coverage_pct": round((total - missing_coordinates) / total * 100, 2) if total else 0.0,
        },
        reference_data={
            "departments": counts.get("departments", 0),
            "service_guidelines": counts.get("service_guidelines", 0),
            "department_provenance": DEPARTMENT_PROVENANCE,
            "guideline_provenance": GUIDELINE_PROVENANCE,
        },
    )
    report.checks = _collect_checks(session, counts)
    return report


def render_markdown(report: DatasetReport) -> str:
    """Render the report as Markdown with the real, measured numbers."""
    lines: list[str] = []
    add = lines.append

    add("# CivicBridge AI - Phase 2 Dataset Report")
    add("")
    add(f"Generated: `{report.generated_at}`")
    add("")
    add("> Every number below was measured against the live PostgreSQL database.")
    add("> The dataset is entirely synthetic; nothing here is real citizen data.")
    add("")

    add("## Database")
    add("")
    add(
        f"- Database: `{report.database['name']}` on {report.database['host']}:{report.database['port']}"
    )
    add(f"- Role: `{report.database['user']}`")
    add(f"- Alembic revision: `{report.migration_revision}`")
    add("")

    add("## Row counts per table")
    add("")
    add("| Table | Rows |")
    add("| --- | ---: |")
    for table in TABLE_NAMES:
        add(f"| {table} | {report.table_counts.get(table, 0)} |")
    add("")

    complaints = report.complaints
    add(f"## Complaints (total: {complaints['total']})")
    add("")

    def distribution_table(title: str, mapping: dict[str, int]) -> None:
        add(f"### {title}")
        add("")
        add("| Value | Count | Share |")
        add("| --- | ---: | ---: |")
        for key, value in sorted(mapping.items(), key=lambda item: -item[1]):
            share = value / complaints["total"] * 100 if complaints["total"] else 0
            add(f"| {key} | {value} | {share:.1f}% |")
        add("")

    distribution_table("Category distribution", complaints["by_category"])
    distribution_table("Language distribution", complaints["by_language"])
    distribution_table("Priority distribution", complaints["by_priority"])
    distribution_table("Status distribution", complaints["by_status"])

    duplicates = report.duplicates
    add("## Planted duplicates")
    add("")
    add(f"- Total duplicates: {duplicates['total']}")
    add(f"- Similarity range: {duplicates['similarity_min']} - {duplicates['similarity_max']}")
    add("")
    if duplicates["examples"]:
        add("| Duplicate | Original | Similarity |")
        add("| --- | --- | ---: |")
        for example in duplicates["examples"]:
            add(
                f"| {example['duplicate']} | {example['original']} | "
                f"{example['similarity'] if example['similarity'] is not None else 'n/a'} |"
            )
        add("")

    coordinates = report.coordinates
    add("## Geolocation coverage")
    add("")
    add(f"- With coordinates: {coordinates['with_coordinates']} ({coordinates['coverage_pct']}%)")
    add(f"- Missing coordinates (NULL pair): {coordinates['missing']}")
    add("")

    add("## Prototype reference data")
    add("")
    add(f"- Departments: {report.reference_data['departments']}")
    add(f"- Service guidelines: {report.reference_data['service_guidelines']}")
    add("")
    add(f"> {report.reference_data['department_provenance']}")
    add("")
    add(f"> {report.reference_data['guideline_provenance']}")
    add("")

    add("## Validation results")
    add("")
    add("| Check | Result | Detail |")
    add("| --- | --- | --- |")
    for check in report.checks:
        add(f"| {check.name} | {'PASS' if check.passed else 'FAIL'} | {check.detail} |")
    add("")
    passed = sum(1 for check in report.checks if check.passed)
    add(f"**{passed}/{len(report.checks)} checks passed.**")
    add("")

    return "\n".join(lines)


def write_report_files(report: DatasetReport, output_dir: Path) -> dict[str, Path]:
    """Write the report as Markdown and JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "dataset_report.md"
    json_path = output_dir / "dataset_report.json"

    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
    return {"markdown": markdown_path, "json": json_path}
