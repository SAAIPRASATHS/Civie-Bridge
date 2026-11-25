"""Phase 2 database tests: migrations, tables, keys and constraints.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase2_database.py -v

The schema assertions are read from PostgreSQL's own catalogue rather than from
SQLAlchemy metadata, so they prove what was actually created by the migration.
Every test that writes is wrapped in a transaction that is always rolled back,
so the seeded dataset is never mutated.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError, IntegrityError

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database.session import check_database, get_engine
from app.models import TABLE_NAMES

database_status = check_database()
requires_database = pytest.mark.skipif(
    not database_status.connected,
    reason="PostgreSQL not reachable; run `python -m app.cli health` first",
)


def _fetch_all(sql: str) -> list[tuple]:
    with get_engine().connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql)).all()]


def _complaint_count() -> int:
    if not database_status.connected:
        return 0
    try:
        return int(_fetch_all("SELECT count(*) FROM complaints")[0][0])
    except Exception:  # table not migrated yet
        return 0


requires_dataset = pytest.mark.skipif(
    _complaint_count() == 0,
    reason="no complaints stored; run `python -m app.cli seed-db` first",
)

EXPECTED_ENUM_TYPES = {
    "agent_execution_status",
    "agent_name",
    "assignment_approval_status",
    "complaint_category",
    "complaint_language",
    "complaint_priority",
    "complaint_status",
    "escalation_status",
    "escalation_trigger",
    "evidence_verification_status",
    "user_role",
}

EXPECTED_CHECK_CONSTRAINTS = {
    "ck_agent_logs_confidence_range",
    "ck_agent_logs_duration_non_negative",
    "ck_agent_logs_step_index_non_negative",
    "ck_assignments_ai_confidence_range",
    "ck_assignments_approved_requires_approver",
    "ck_assignments_approved_requires_timestamp",
    "ck_complaints_classification_confidence_range",
    "ck_complaints_coordinates_paired",
    "ck_complaints_duplicate_similarity_range",
    "ck_complaints_latitude_range",
    "ck_complaints_longitude_range",
    "ck_complaints_not_self_duplicate",
    "ck_complaints_resolved_after_submitted",
    "ck_complaints_urgency_score_range",
    "ck_escalation_approved_requires_approver",
    "ck_escalation_executed_requires_timestamp",
    "ck_escalation_level_positive",
    "ck_escalation_probability_range",
    "ck_evidence_file_size_positive",
    "ck_evidence_relevance_score_range",
    "ck_guidelines_sla_hours_positive",
    "ck_officers_employee_code_not_blank",
    "ck_officers_max_active_cases_positive",
}

EXPECTED_FOREIGN_KEYS = {
    ("complaints", "citizen_id", "users", "id"),
    ("complaints", "current_department_id", "departments", "id"),
    ("complaints", "duplicate_of_id", "complaints", "id"),
    ("officers", "user_id", "users", "id"),
    ("officers", "department_id", "departments", "id"),
    ("service_guidelines", "department_id", "departments", "id"),
    ("complaint_evidence", "complaint_id", "complaints", "id"),
    ("complaint_assignments", "complaint_id", "complaints", "id"),
    ("complaint_assignments", "department_id", "departments", "id"),
    ("complaint_assignments", "officer_id", "officers", "id"),
    ("complaint_assignments", "approved_by_user_id", "users", "id"),
    ("complaint_status_history", "complaint_id", "complaints", "id"),
    ("complaint_status_history", "changed_by_user_id", "users", "id"),
    ("agent_execution_logs", "complaint_id", "complaints", "id"),
    ("escalation_records", "complaint_id", "complaints", "id"),
    ("escalation_records", "approved_by_user_id", "users", "id"),
}


@contextmanager
def _rollback_transaction() -> Iterator:
    """Yield a connection whose transaction is always rolled back."""
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@requires_database
def test_migration_revision_is_applied() -> None:
    revision = _fetch_all("SELECT version_num FROM alembic_version")
    assert revision, "no Alembic revision recorded; run `python -m alembic upgrade head`"
    assert revision[0][0]


@requires_database
def test_all_ten_tables_exist() -> None:
    rows = _fetch_all("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    present = {row[0] for row in rows}
    missing = [name for name in TABLE_NAMES if name not in present]
    assert not missing, f"missing tables: {missing}"


@requires_database
def test_all_native_enum_types_exist() -> None:
    rows = _fetch_all("SELECT typname FROM pg_type WHERE typtype = 'e'")
    present = {row[0] for row in rows}
    missing = EXPECTED_ENUM_TYPES - present
    assert not missing, f"missing enum types: {sorted(missing)}"


@requires_database
def test_every_table_has_a_uuid_primary_key() -> None:
    rows = _fetch_all(
        """
        SELECT table_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'id'
        """
    )
    by_table = dict(rows)
    for table in TABLE_NAMES:
        assert by_table.get(table) == "uuid", f"{table}.id is not uuid"


@requires_database
def test_created_at_and_updated_at_on_every_table() -> None:
    rows = _fetch_all(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name IN ('created_at', 'updated_at')
        """
    )
    columns: dict[str, set[str]] = {}
    for table, column in rows:
        columns.setdefault(table, set()).add(column)
    for table in TABLE_NAMES:
        assert columns.get(table) == {"created_at", "updated_at"}, (
            f"{table} is missing timestamp columns"
        )


@requires_database
def test_check_constraints_are_declared() -> None:
    rows = _fetch_all(
        "SELECT constraint_name FROM information_schema.table_constraints "
        "WHERE constraint_schema = 'public' AND constraint_type = 'CHECK'"
    )
    present = {row[0] for row in rows}
    missing = EXPECTED_CHECK_CONSTRAINTS - present
    assert not missing, f"missing check constraints: {sorted(missing)}"


@requires_database
def test_foreign_keys_are_declared() -> None:
    rows = _fetch_all(
        """
        SELECT
            tc.table_name,
            kcu.column_name,
            ccu.table_name AS foreign_table,
            ccu.column_name AS foreign_column
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
        """
    )
    present = {(row[0], row[1], row[2], row[3]) for row in rows}
    missing = EXPECTED_FOREIGN_KEYS - present
    assert not missing, f"missing foreign keys: {sorted(missing)}"


@requires_database
def test_expected_indexes_exist() -> None:
    rows = _fetch_all("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    present = {row[0] for row in rows}
    for index_name in (
        "ix_complaints_category_status",
        "ix_complaints_language",
        "ix_complaints_duplicate_of",
        "ix_departments_category_active",
        "ix_officers_department_available",
        "ix_guidelines_category_active",
        "ix_agent_logs_complaint_step",
        "ix_escalations_complaint",
        "ix_status_history_complaint_changed",
    ):
        assert index_name in present, f"missing index {index_name}"


# ---------------------------------------------------------------------------
# Constraint enforcement - every write below happens inside a rolled-back
# transaction, so the seeded dataset is left untouched. Each test asserts the
# *specific* constraint that fired, so an unrelated NOT NULL violation cannot
# masquerade as a passing check.
# ---------------------------------------------------------------------------


def _insert_complaint_sql(columns: str = "", values: str = "") -> str:
    """Build a raw INSERT that satisfies every NOT NULL column.

    ``sla_breached`` / ``needs_clarification`` / ``is_synthetic`` are NOT NULL
    with Python-side defaults only, so raw SQL must supply them explicitly.
    """
    all_columns = (
        "reference_id, description, language, status, sla_breached, "
        "needs_clarification, is_synthetic"
    )
    all_values = "'CB-TEST', 'raw sql constraint probe', 'en', 'submitted', false, false, true"
    if columns:
        all_columns = f"{all_columns}, {columns}"
        all_values = f"{all_values}, {values}"
    return f"INSERT INTO complaints ({all_columns}) VALUES ({all_values})"


@requires_database
def test_invalid_status_value_is_rejected() -> None:
    with _rollback_transaction() as connection:
        with pytest.raises(DatabaseError) as excinfo:
            connection.execute(
                text(
                    "INSERT INTO complaints "
                    "(reference_id, description, language, status, sla_breached, "
                    "needs_clarification, is_synthetic) "
                    "VALUES ('CB-TEST-STATUS', 'test', 'en', 'not_a_real_status', "
                    "false, false, true)"
                )
            )
        assert "not_a_real_status" in str(excinfo.value)


@requires_database
def test_half_coordinate_pair_is_rejected() -> None:
    with _rollback_transaction() as connection:
        with pytest.raises(IntegrityError) as excinfo:
            connection.execute(text(_insert_complaint_sql("latitude", "13.05")))
        assert "ck_complaints_coordinates_paired" in str(excinfo.value)


@requires_database
def test_out_of_range_latitude_is_rejected() -> None:
    with _rollback_transaction() as connection:
        with pytest.raises(IntegrityError) as excinfo:
            connection.execute(text(_insert_complaint_sql("latitude, longitude", "999.0, 80.2")))
        assert "ck_complaints_latitude_range" in str(excinfo.value)


@requires_database
def test_self_referential_duplicate_is_rejected() -> None:
    with _rollback_transaction() as connection:
        connection.execute(text(_insert_complaint_sql()))
        with pytest.raises(IntegrityError) as excinfo:
            connection.execute(
                text("UPDATE complaints SET duplicate_of_id = id WHERE reference_id = 'CB-TEST'")
            )
        assert "ck_complaints_not_self_duplicate" in str(excinfo.value)


@requires_database
def test_unknown_citizen_foreign_key_is_rejected() -> None:
    with _rollback_transaction() as connection:
        with pytest.raises(IntegrityError) as excinfo:
            connection.execute(
                text(_insert_complaint_sql("citizen_id", "'00000000-0000-0000-0000-000000000000'"))
            )
        assert "citizen_id" in str(excinfo.value)


@requires_database
@requires_dataset
def test_approved_assignment_requires_an_approver() -> None:
    with _rollback_transaction() as connection:
        row = connection.execute(
            text("SELECT c.id, d.id FROM complaints c CROSS JOIN departments d LIMIT 1")
        ).one()
        with pytest.raises(IntegrityError) as excinfo:
            connection.execute(
                text(
                    "INSERT INTO complaint_assignments "
                    "(complaint_id, department_id, approval_status) "
                    "VALUES (:c, :d, 'approved')"
                ),
                {"c": row[0], "d": row[1]},
            )
        assert "ck_assignments_approved_requires_approver" in str(excinfo.value)


@requires_database
@requires_dataset
def test_duplicate_escalation_level_is_rejected() -> None:
    with _rollback_transaction() as connection:
        complaint_id = connection.execute(text("SELECT id FROM complaints LIMIT 1")).scalar_one()
        insert_sql = text(
            "INSERT INTO escalation_records "
            "(complaint_id, escalation_level, trigger, status, reason, requires_human_approval) "
            "VALUES (:c, 1, 'sla_breach_predicted', 'recommended', 'test', true)"
        )
        connection.execute(insert_sql, {"c": complaint_id})
        with pytest.raises(IntegrityError) as excinfo:
            connection.execute(insert_sql, {"c": complaint_id})
        assert "uq_escalation_complaint_level" in str(excinfo.value)
