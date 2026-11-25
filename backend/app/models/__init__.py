"""SQLAlchemy ORM models for CivicBridge AI.

Importing this package registers every table on ``Base.metadata``, which is
what Alembic autogenerate and ``Base.metadata.create_all`` rely on.
"""

from app.models.agent_execution_log import AgentExecutionLog
from app.models.assignment import ComplaintAssignment
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.complaint import Complaint
from app.models.complaint_evidence import ComplaintEvidence
from app.models.department import Department
from app.models.enums import (
    AgentExecutionStatus,
    AgentName,
    AssignmentApprovalStatus,
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    ComplaintStatus,
    EscalationStatus,
    EscalationTrigger,
    EvidenceVerificationStatus,
    UserRole,
    enum_values,
)
from app.models.escalation import EscalationRecord
from app.models.officer import Officer
from app.models.service_guideline import ServiceGuideline
from app.models.status_history import ComplaintStatusHistory
from app.models.user import User

#: Canonical table names in creation order (parents before children), used by
#: tests and the dataset report so both agree on what "all tables" means.
TABLE_NAMES: tuple[str, ...] = (
    "users",
    "departments",
    "officers",
    "complaints",
    "complaint_evidence",
    "complaint_assignments",
    "complaint_status_history",
    "agent_execution_logs",
    "escalation_records",
    "service_guidelines",
)

__all__ = [
    "TABLE_NAMES",
    "AgentExecutionLog",
    "AgentExecutionStatus",
    "AgentName",
    "AssignmentApprovalStatus",
    "Complaint",
    "ComplaintAssignment",
    "ComplaintCategory",
    "ComplaintEvidence",
    "ComplaintLanguage",
    "ComplaintPriority",
    "ComplaintStatus",
    "ComplaintStatusHistory",
    "Department",
    "EscalationRecord",
    "EscalationStatus",
    "EscalationTrigger",
    "EvidenceVerificationStatus",
    "Officer",
    "ServiceGuideline",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserRole",
    "enum_values",
]
