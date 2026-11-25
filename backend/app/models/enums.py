"""Controlled vocabularies shared by the ORM models and the agents.

Every enum is backed by a native PostgreSQL type so invalid values are rejected
at the database boundary, not just in Python. Values are stored as their
lower-case string form, which keeps the stored data readable and stable even if
the Python member names are ever renamed.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ComplaintCategory(enum.StrEnum):
    """The four civic problem categories supported by the prototype."""

    WASTE_MANAGEMENT = "waste_management"
    ROAD_MAINTENANCE = "road_maintenance"
    WATER_SUPPLY = "water_supply"
    STREET_LIGHTING = "street_lighting"


class ComplaintPriority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplaintStatus(enum.StrEnum):
    """Lifecycle of a complaint.

    ``DUPLICATE`` and ``REJECTED`` are terminal. ``CLOSED`` is only reachable
    through an officer decision - no agent may set it.
    """

    SUBMITTED = "submitted"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    UNDER_REVIEW = "under_review"
    AWAITING_APPROVAL = "awaiting_approval"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    ESCALATED = "escalated"


class ComplaintLanguage(enum.StrEnum):
    """``TA_LATN`` = Tamil written in Latin script (transliterated)."""

    EN = "en"
    TA = "ta"
    TA_LATN = "ta-latn"


class UserRole(enum.StrEnum):
    CITIZEN = "citizen"
    OFFICER = "officer"
    ADMIN = "admin"


class EvidenceVerificationStatus(enum.StrEnum):
    PENDING = "pending"
    VERIFIED_RELEVANT = "verified_relevant"
    UNCERTAIN = "uncertain"
    NOT_RELEVANT = "not_relevant"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class AssignmentApprovalStatus(enum.StrEnum):
    """Human-in-the-loop state of a routing recommendation."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REASSIGNED = "reassigned"


class AgentName(enum.StrEnum):
    INTAKE = "intake"
    CLASSIFICATION = "classification"
    DUPLICATE_DETECTION = "duplicate_detection"
    EVIDENCE_VERIFICATION = "evidence_verification"
    DEPARTMENT_ROUTING = "department_routing"
    RESOLUTION_RECOMMENDATION = "resolution_recommendation"
    SLA_ESCALATION = "sla_escalation"


class AgentExecutionStatus(enum.StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_HUMAN = "awaiting_human"


class EscalationStatus(enum.StrEnum):
    RECOMMENDED = "recommended"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class EscalationTrigger(enum.StrEnum):
    SLA_BREACH_PREDICTED = "sla_breach_predicted"
    SLA_DEADLINE_BREACHED = "sla_deadline_breached"
    REPEATED_COMPLAINT = "repeated_complaint"
    MANUAL = "manual"


def pg_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """Build a native PostgreSQL enum column type bound to ``name``.

    ``values_callable`` makes the database store the enum *values*
    (``waste_management``) rather than the member names (``WASTE_MANAGEMENT``),
    and ``create_type=False`` is intentionally avoided so Alembic can emit the
    ``CREATE TYPE`` statements during the initial migration.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


#: Column types reused across models, kept here so the PostgreSQL type names
#: stay consistent between the ORM metadata and the Alembic migration.
COMPLAINT_CATEGORY = pg_enum(ComplaintCategory, "complaint_category")
COMPLAINT_PRIORITY = pg_enum(ComplaintPriority, "complaint_priority")
COMPLAINT_STATUS = pg_enum(ComplaintStatus, "complaint_status")
COMPLAINT_LANGUAGE = pg_enum(ComplaintLanguage, "complaint_language")
USER_ROLE = pg_enum(UserRole, "user_role")
EVIDENCE_VERIFICATION_STATUS = pg_enum(EvidenceVerificationStatus, "evidence_verification_status")
ASSIGNMENT_APPROVAL_STATUS = pg_enum(AssignmentApprovalStatus, "assignment_approval_status")
AGENT_NAME = pg_enum(AgentName, "agent_name")
AGENT_EXECUTION_STATUS = pg_enum(AgentExecutionStatus, "agent_execution_status")
ESCALATION_STATUS = pg_enum(EscalationStatus, "escalation_status")
ESCALATION_TRIGGER = pg_enum(EscalationTrigger, "escalation_trigger")


def enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return the stored string values of an enum, for validation and reporting."""
    return [member.value for member in enum_cls]
