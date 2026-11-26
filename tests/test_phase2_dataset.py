"""Phase 2 dataset tests: generator invariants and stored-data validation.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase2_dataset.py -v

The generator tests run without any service. The stored-data tests read the
seeded ``complaints`` table and are skipped until ``python -m app.cli seed-db``
has been run against a reachable PostgreSQL instance.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database.session import check_database, get_engine
from app.datasets import (
    DEPARTMENT_DIRECTORY,
    DEPARTMENT_PROVENANCE,
    GUIDELINE_PROVENANCE,
    SERVICE_GUIDELINES,
    build_report,
    generate_complaints,
    summarise,
)
from app.datasets.generator import (
    DEFAULT_DUPLICATE_COUNT,
    MISSING_COORDINATE_RATE,
    TEMPLATES,
)
from app.models.enums import (
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    ComplaintStatus,
    enum_values,
)

CATEGORIES = set(enum_values(ComplaintCategory))
LANGUAGES = set(enum_values(ComplaintLanguage))
PRIORITIES = set(enum_values(ComplaintPriority))
STATUSES = set(enum_values(ComplaintStatus))

# ---------------------------------------------------------------------------
# Generator unit tests - no services required
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated():
    return generate_complaints()


def test_generator_is_deterministic() -> None:
    first = generate_complaints(count=200, duplicate_count=10, seed=1234)
    second = generate_complaints(count=200, duplicate_count=10, seed=1234)
    assert [c.reference_id for c in first] == [c.reference_id for c in second]
    assert [c.description for c in first] == [c.description for c in second]

    other_seed = generate_complaints(count=200, duplicate_count=10, seed=9999)
    assert [c.description for c in other_seed] != [c.description for c in first]


def test_generator_produces_at_least_one_thousand_complaints(generated) -> None:
    summary = summarise(generated)
    assert summary.total >= 1000
    assert summary.duplicates == DEFAULT_DUPLICATE_COUNT
    assert summary.primary == summary.total - summary.duplicates


def test_generator_reference_ids_are_unique(generated) -> None:
    reference_ids = [c.reference_id for c in generated]
    assert len(reference_ids) == len(set(reference_ids))
    assert all(len(reference_id) <= 24 for reference_id in reference_ids)


def test_generator_covers_all_categories_and_languages(generated) -> None:
    summary = summarise(generated)
    assert set(summary.by_category) == CATEGORIES
    assert set(summary.by_language) == LANGUAGES


def test_category_distribution_is_balanced(generated) -> None:
    summary = summarise(generated)
    for category in CATEGORIES:
        share = summary.by_category[category] / summary.total
        assert 0.10 <= share <= 0.45, f"{category} share {share:.3f} outside expected band"


def test_language_distribution_covers_english_tamil_and_transliterated(generated) -> None:
    summary = summarise(generated)
    for language in LANGUAGES:
        share = summary.by_language[language] / summary.total
        assert share >= 0.05, f"{language} share {share:.3f} is too small"
    # English remains the majority language, as expected for a mixed dataset.
    assert summary.by_language[ComplaintLanguage.EN.value] > summary.total / 3


def test_priority_and_status_vocabularies_are_used(generated) -> None:
    summary = summarise(generated)
    assert set(summary.by_priority) <= PRIORITIES
    assert set(summary.by_status) <= STATUSES
    assert set(summary.by_priority) == PRIORITIES
    assert ComplaintStatus.DUPLICATE.value in summary.by_status


def test_planted_duplicates_reference_real_complaints(generated) -> None:
    reference_ids = {c.reference_id for c in generated}
    duplicates = [c for c in generated if c.duplicate_of_reference_id]

    assert duplicates, "the generator should plant duplicate complaints"
    for duplicate in duplicates:
        assert duplicate.duplicate_of_reference_id in reference_ids
        assert duplicate.duplicate_of_reference_id != duplicate.reference_id
        assert duplicate.status == ComplaintStatus.DUPLICATE.value
        assert duplicate.duplicate_similarity is not None
        assert 0.0 <= duplicate.duplicate_similarity <= 1.0


def test_missing_coordinates_are_always_paired(generated) -> None:
    without_location = [c for c in generated if c.latitude is None]
    assert without_location, "some complaints should arrive without a location fix"

    for complaint in generated:
        assert (complaint.latitude is None) == (complaint.longitude is None)
        if complaint.latitude is not None:
            assert -90 <= complaint.latitude <= 90
            assert -180 <= complaint.longitude <= 180

    share = len(without_location) / len(generated)
    assert share == pytest.approx(MISSING_COORDINATE_RATE, abs=0.05)


def test_templates_cover_every_category_and_language() -> None:
    for category in ComplaintCategory:
        assert category in TEMPLATES, f"no templates for {category}"
        for language in ComplaintLanguage:
            assert language in TEMPLATES[category], f"no {language} template for {category}"
            assert TEMPLATES[category][language], f"empty {language} template set for {category}"


def test_reference_data_is_labelled_as_prototype() -> None:
    assert len(DEPARTMENT_DIRECTORY) == len(CATEGORIES)
    assert {dept["category"] for dept in DEPARTMENT_DIRECTORY} == {
        ComplaintCategory(value) for value in CATEGORIES
    }
    assert "prototype" in DEPARTMENT_PROVENANCE.lower()
    assert "not" in DEPARTMENT_PROVENANCE.lower()

    for guideline in SERVICE_GUIDELINES:
        assert guideline["sla_hours"] > 0
        assert guideline["resolution_steps"]

    assert "prototype" in GUIDELINE_PROVENANCE.lower()
    assert "not" in GUIDELINE_PROVENANCE.lower()


def test_generator_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        generate_complaints(count=0)
    with pytest.raises(ValueError):
        generate_complaints(duplicate_count=-1)


# ---------------------------------------------------------------------------
# Stored-data validation - requires PostgreSQL and a seeded dataset
# ---------------------------------------------------------------------------

database_status = check_database()


def _rows(sql: str) -> list[tuple]:
    with get_engine().connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql)).all()]


def _stored_complaint_count() -> int:
    if not database_status.connected:
        return 0
    try:
        return int(_rows("SELECT count(*) FROM complaints")[0][0])
    except Exception:
        return 0


requires_dataset = pytest.mark.skipif(
    not database_status.connected or _stored_complaint_count() == 0,
    reason="PostgreSQL unavailable or dataset not seeded; run `python -m app.cli seed-db`",
)


@requires_dataset
def test_stored_dataset_meets_minimum_size() -> None:
    total = _rows("SELECT count(*) FROM complaints WHERE is_synthetic IS TRUE")[0][0]
    assert int(total) >= 1000, f"only {total} synthetic complaints stored"


@requires_dataset
def test_stored_category_distribution() -> None:
    rows = _rows(
        "SELECT category::text, count(*) FROM complaints "
        "WHERE is_synthetic IS TRUE GROUP BY 1"
    )
    counts = {row[0]: int(row[1]) for row in rows}
    total = sum(counts.values())

    assert set(counts) == CATEGORIES, f"stored categories: {sorted(counts)}"
    for category, count in counts.items():
        assert count / total >= 0.05, f"{category} has only {count} rows"


@requires_dataset
def test_stored_language_distribution() -> None:
    rows = _rows(
        "SELECT language::text, count(*) FROM complaints "
        "WHERE is_synthetic IS TRUE GROUP BY 1"
    )
    counts = {row[0]: int(row[1]) for row in rows}
    total = sum(counts.values())

    assert set(counts) == LANGUAGES, f"stored languages: {sorted(counts)}"
    for language, count in counts.items():
        assert count / total >= 0.05, f"{language} has only {count} rows"


@requires_dataset
def test_stored_duplicates_resolve_to_real_complaints() -> None:
    duplicates = _rows("SELECT count(*) FROM complaints WHERE duplicate_of_id IS NOT NULL")[0][0]
    assert duplicates > 0, "no planted duplicates were stored"

    orphaned = _rows(
        "SELECT count(*) FROM complaints c WHERE c.duplicate_of_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM complaints o WHERE o.id = c.duplicate_of_id)"
    )[0][0]
    assert orphaned == 0, f"{orphaned} duplicates point at a missing original"


@requires_dataset
def test_stored_missing_coordinates_and_validity() -> None:
    missing = _rows("SELECT count(*) FROM complaints WHERE latitude IS NULL AND longitude IS NULL")[
        0
    ][0]
    assert missing > 0, "expected some complaints without a location fix"

    half_pairs = _rows(
        "SELECT count(*) FROM complaints WHERE (latitude IS NULL) <> (longitude IS NULL)"
    )[0][0]
    assert half_pairs == 0, f"{half_pairs} complaints store an incomplete coordinate pair"

    out_of_range = _rows(
        "SELECT count(*) FROM complaints WHERE "
        "(latitude IS NOT NULL AND (latitude < -90 OR latitude > 90)) OR "
        "(longitude IS NOT NULL AND (longitude < -180 OR longitude > 180))"
    )[0][0]
    assert out_of_range == 0, f"{out_of_range} complaints store invalid coordinates"


@requires_dataset
def test_stored_status_values_are_valid() -> None:
    rows = _rows("SELECT DISTINCT status::text FROM complaints")
    stored = {row[0] for row in rows}
    assert stored <= STATUSES, f"unexpected statuses: {sorted(stored - STATUSES)}"


@requires_dataset
def test_stored_reference_data_is_labelled() -> None:
    total_departments, prototype_departments = _rows(
        "SELECT count(*), count(*) FILTER (WHERE is_prototype_data) FROM departments"
    )[0]
    assert total_departments > 0
    assert prototype_departments == total_departments

    total_guidelines, verified_guidelines = _rows(
        "SELECT count(*), count(*) FILTER (WHERE is_verified) FROM service_guidelines"
    )[0]
    assert total_guidelines > 0
    assert verified_guidelines == 0, "no prototype guideline may claim official verification"


@requires_dataset
def test_report_validation_checks_all_pass() -> None:
    from app.database.session import session_scope

    with session_scope() as session:
        report = build_report(session)

    failed = [check.name for check in report.checks if not check.passed]
    assert not failed, f"dataset report checks failed: {failed}"
    assert report.migration_revision, "report should record the applied migration revision"
