"""Seed the database with prototype reference data and the synthetic dataset.

Seeding is split so each step stays independently testable:

* :func:`seed_reference_data` - the controlled department directory and the
  configurable service guidelines. Idempotent: rows are matched on ``code``.
* :func:`seed_users` - synthetic citizens, officers and one administrator.
* :func:`seed_complaints` - the generated complaints plus one audit-trail row
  each, written through the ORM so every constraint is enforced.
* :func:`seed_database` - the end-to-end orchestration used by the CLI.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.datasets import generator
from app.datasets.generator import SyntheticComplaint, summarise
from app.datasets.reference_data import (
    DEPARTMENT_DIRECTORY,
    DEPARTMENT_PROVENANCE,
    GUIDELINE_PROVENANCE,
    SERVICE_GUIDELINES,
)
from app.models import (
    TABLE_NAMES,
    Complaint,
    ComplaintStatus,
    ComplaintStatusHistory,
    Department,
    Officer,
    ServiceGuideline,
    User,
    UserRole,
)
from app.models.enums import ComplaintCategory, ComplaintLanguage

#: Phase 2 shipped a deliberately invalid placeholder hash. Phase 7 replaces it
#: with a real scrypt hash so the synthetic accounts can actually sign in. The
#: value is kept as a named constant only so the old marker is easy to grep for;
#: no row is seeded with it any more.
LEGACY_PLACEHOLDER_PASSWORD_HASH = "prototype:no-auth-configured"

#: Known demo passwords for the synthetic accounts, taken from configuration so
#: a reviewer can sign in without a real account. They are not secrets and the
#: README states plainly that they must change before any real use.
DEMO_PASSWORDS: dict[UserRole, str] = {
    UserRole.CITIZEN: settings.auth_citizen_password,
    UserRole.OFFICER: settings.auth_officer_password,
    UserRole.ADMIN: settings.auth_admin_password,
}

#: Number of synthetic citizen accounts created and linked to complaints.
CITIZEN_COUNT = 25

#: Officers created per department.
OFFICERS_PER_DEPARTMENT = 2


@dataclass
class SeedReport:
    """Row counts written by one seeding run."""

    departments: int = 0
    service_guidelines: int = 0
    users: int = 0
    officers: int = 0
    complaints: int = 0
    status_history: int = 0
    duplicates: int = 0
    missing_coordinates: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "departments": self.departments,
            "service_guidelines": self.service_guidelines,
            "users": self.users,
            "officers": self.officers,
            "complaints": self.complaints,
            "status_history": self.status_history,
            "duplicates": self.duplicates,
            "missing_coordinates": self.missing_coordinates,
        }


def reset_all_data(session: Session) -> None:
    """Remove every row from the ten CivicBridge tables.

    Only safe because every row in this prototype is synthetic. ``TRUNCATE ...
    CASCADE`` handles the foreign-key graph in one statement.
    """
    tables = ", ".join(TABLE_NAMES)
    session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    session.flush()


def seed_reference_data(session: Session) -> tuple[int, int]:
    """Upsert the prototype department directory and service guidelines."""
    departments: dict[str, Department] = {
        dept.code: dept for dept in session.scalars(select(Department)).all()
    }
    for record in DEPARTMENT_DIRECTORY:
        department = departments.get(record["code"])
        if department is None:
            department = Department(code=record["code"])
            session.add(department)
            departments[record["code"]] = department
        department.name = record["name"]
        department.description = record["description"]
        department.category = record["category"]
        department.contact_email = record["contact_email"]
        department.contact_phone = record["contact_phone"]
        department.office_address = record["office_address"]
        department.jurisdiction = record["jurisdiction"]
        department.is_prototype_data = True
        department.data_source = DEPARTMENT_PROVENANCE
        department.is_active = True
    session.flush()

    guidelines: dict[str, ServiceGuideline] = {
        guide.code: guide for guide in session.scalars(select(ServiceGuideline)).all()
    }
    for record in SERVICE_GUIDELINES:
        guideline = guidelines.get(record["code"])
        if guideline is None:
            guideline = ServiceGuideline(code=record["code"])
            session.add(guideline)
            guidelines[record["code"]] = guideline
        guideline.department_id = departments[record["department_code"]].id
        guideline.category = record["category"]
        guideline.title = record["title"]
        guideline.description = record["description"]
        guideline.sla_hours = record["sla_hours"]
        guideline.resolution_steps = list(record["resolution_steps"])
        guideline.escalation_matrix = dict(record["escalation_matrix"])
        guideline.required_evidence = list(record["required_evidence"])
        guideline.applicable_priority = record["applicable_priority"]
        guideline.citizen_response_template = record["citizen_response_template"]
        # Prototype assumption: never claim this is an official service rule.
        guideline.is_verified = False
        guideline.verification_source = GUIDELINE_PROVENANCE
        guideline.is_active = True
    session.flush()

    return len(DEPARTMENT_DIRECTORY), len(SERVICE_GUIDELINES)


def seed_users(session: Session) -> tuple[list[User], list[Officer]]:
    """Create synthetic citizens, officers (per department) and one admin.

    Every account is given a real scrypt hash of its role's documented demo
    password, so the prototype can be signed into. No hash is ever returned or
    logged, and the passwords are synthetic - not a real person's credentials.
    """
    departments = session.scalars(select(Department).order_by(Department.code)).all()
    if not departments:
        raise RuntimeError("seed_reference_data must run before seed_users")

    citizen_password = DEMO_PASSWORDS[UserRole.CITIZEN]
    admin_password = DEMO_PASSWORDS[UserRole.ADMIN]
    officer_password = DEMO_PASSWORDS[UserRole.OFFICER]

    citizens: list[User] = []
    for index in range(1, CITIZEN_COUNT + 1):
        citizens.append(
            User(
                email=f"citizen{index:03d}@civicbridge.prototype",
                phone=f"+91-90000-{index:05d}",
                full_name=f"Synthetic Citizen {index:03d}",
                password_hash=hash_password(citizen_password),
                role=UserRole.CITIZEN,
                preferred_language=(ComplaintLanguage.EN if index % 3 else ComplaintLanguage.TA),
                is_active=True,
            )
        )
    session.add_all(citizens)

    admin = User(
        email="admin@civicbridge.prototype",
        phone="+91-90000-00000",
        full_name="Synthetic Administrator",
        password_hash=hash_password(admin_password),
        role=UserRole.ADMIN,
        preferred_language=ComplaintLanguage.EN,
        is_active=True,
    )
    session.add(admin)

    officer_users: list[User] = []
    officers: list[Officer] = []
    for department in departments:
        for slot in range(1, OFFICERS_PER_DEPARTMENT + 1):
            code = department.code.lower()
            user = User(
                email=f"officer.{code}{slot}@civicbridge.prototype",
                phone=f"+91-90001-{len(officer_users) + 1:05d}",
                full_name=f"Synthetic {department.code} Officer {slot}",
                password_hash=hash_password(officer_password),
                role=UserRole.OFFICER,
                preferred_language=ComplaintLanguage.EN,
                is_active=True,
            )
            officer_users.append(user)
            officers.append(
                Officer(
                    user=user,
                    department=department,
                    employee_code=f"{department.code}-OFF-{slot:02d}",
                    designation="Assistant Engineer (Prototype)",
                    jurisdiction=department.jurisdiction,
                    max_active_cases=25,
                    is_available=True,
                )
            )
    session.add_all(officer_users)
    session.flush()  # officer.user_id must exist before the profiles are read back

    return [*citizens, admin, *officer_users], officers


#: Statuses that imply a department has already taken ownership of a complaint.
_ASSIGNED_STATUSES = {
    ComplaintStatus.ASSIGNED,
    ComplaintStatus.IN_PROGRESS,
    ComplaintStatus.ESCALATED,
    ComplaintStatus.RESOLVED,
    ComplaintStatus.CLOSED,
}


def seed_complaints(
    session: Session,
    complaints: list[SyntheticComplaint],
    citizens: list[User],
) -> tuple[int, int]:
    """Insert generated complaints and one audit-trail row per complaint."""
    department_by_category = {
        department.category: department for department in session.scalars(select(Department)).all()
    }
    rng = random.Random(generator.DEFAULT_SEED)

    history_rows: list[ComplaintStatusHistory] = []
    for generated in complaints:
        category = ComplaintCategory(generated.category)
        status = ComplaintStatus(generated.status)
        citizen = rng.choice(citizens) if citizens else None
        department = department_by_category.get(category) if status in _ASSIGNED_STATUSES else None

        complaint = Complaint(
            reference_id=generated.reference_id,
            citizen_id=citizen.id if citizen else None,
            title=generated.title,
            description=generated.description,
            language=ComplaintLanguage(generated.language),
            location_text=generated.location_text,
            ward=generated.ward,
            latitude=generated.latitude,
            longitude=generated.longitude,
            location_accuracy_m=generated.location_accuracy_m,
            status=status,
            category=category,
            priority=generated.priority,
            current_department_id=department.id if department else None,
            reported_at=generated.reported_at,
            submitted_at=generated.submitted_at,
            sla_due_at=generated.sla_due_at,
            sla_breached=generated.sla_breached,
            resolved_at=generated.resolved_at,
            closed_at=generated.closed_at,
            needs_clarification=generated.needs_clarification,
            clarification_question=generated.clarification_question,
            duplicate_similarity=generated.duplicate_similarity,
            is_synthetic=True,
        )
        session.add(complaint)
        session.flush()  # populates complaint.id from the server default

        if generated.duplicate_of_reference_id:
            original_id = session.execute(
                select(Complaint.id).where(
                    Complaint.reference_id == generated.duplicate_of_reference_id
                )
            ).scalar_one()
            complaint.duplicate_of_id = original_id

        history_rows.append(
            ComplaintStatusHistory(
                complaint_id=complaint.id,
                from_status=None,
                to_status=ComplaintStatus.SUBMITTED,
                changed_by_agent="intake",
                reason="Complaint registered from the synthetic dataset",
                changed_at=generated.submitted_at,
            )
        )
        if status is not ComplaintStatus.SUBMITTED:
            history_rows.append(
                ComplaintStatusHistory(
                    complaint_id=complaint.id,
                    from_status=ComplaintStatus.SUBMITTED,
                    to_status=status,
                    changed_by_agent="synthetic_backfill",
                    reason="Historical state planted by the Phase 2 dataset generator",
                    changed_at=generated.submitted_at,
                )
            )

    session.add_all(history_rows)
    session.flush()
    return len(complaints), len(history_rows)


def seed_database(
    session: Session,
    *,
    count: int = generator.DEFAULT_COUNT,
    duplicate_count: int = generator.DEFAULT_DUPLICATE_COUNT,
    seed: int = generator.DEFAULT_SEED,
    reset: bool = True,
) -> tuple[SeedReport, list[SyntheticComplaint]]:
    """Full seeding pipeline: reset, reference data, users, then complaints."""
    if reset:
        reset_all_data(session)

    report = SeedReport()
    report.departments, report.service_guidelines = seed_reference_data(session)
    users, officers = seed_users(session)
    report.users = len(users)
    report.officers = len(officers)

    complaints = generator.generate_complaints(
        count=count, duplicate_count=duplicate_count, seed=seed
    )
    citizens = [user for user in users if user.role is UserRole.CITIZEN]
    report.complaints, report.status_history = seed_complaints(session, complaints, citizens)

    summary = summarise(complaints)
    report.duplicates = summary.duplicates
    report.missing_coordinates = summary.missing_coordinates

    session.flush()
    return report, complaints


def table_row_counts(session: Session) -> dict[str, int]:
    """Return the current row count of each CivicBridge table."""
    counts: dict[str, int] = {}
    for table in TABLE_NAMES:
        counts[table] = session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    return counts


def database_revision(session: Session) -> str | None:
    """Return the Alembic revision currently applied to the database."""
    try:
        return session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        session.rollback()
        return None
