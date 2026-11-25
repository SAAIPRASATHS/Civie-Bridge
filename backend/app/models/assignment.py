"""Department/officer assignment records with human-in-the-loop approval.

The routing agent writes a *recommendation* into ``ai_*`` columns and leaves
``approval_status`` at ``pending``. Only an officer action may move it to
``approved`` - this is the structural guarantee that no agent assigns work on
its own.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ASSIGNMENT_APPROVAL_STATUS,
    AssignmentApprovalStatus,
)

if TYPE_CHECKING:
    from app.models.complaint import Complaint
    from app.models.department import Department
    from app.models.officer import Officer


class ComplaintAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One routing recommendation and its approval outcome."""

    __tablename__ = "complaint_assignments"

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    officer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("officers.id", ondelete="SET NULL")
    )

    # Agent recommendation, kept separate from the approved outcome.
    ai_recommended_department_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL")
    )
    ai_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    ai_recommended_officer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("officers.id", ondelete="SET NULL")
    )

    # Human decision.
    approval_status: Mapped[AssignmentApprovalStatus] = mapped_column(
        ASSIGNMENT_APPROVAL_STATUS,
        nullable=False,
        default=AssignmentApprovalStatus.PENDING,
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    complaint: Mapped[Complaint] = relationship(back_populates="assignments")
    department: Mapped[Department] = relationship(foreign_keys=[department_id])
    officer: Mapped[Officer | None] = relationship(foreign_keys=[officer_id])

    __table_args__ = (
        CheckConstraint(
            "ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)",
            name="ck_assignments_ai_confidence_range",
        ),
        CheckConstraint(
            "approval_status <> 'approved' OR approved_by_user_id IS NOT NULL",
            name="ck_assignments_approved_requires_approver",
        ),
        CheckConstraint(
            "approval_status <> 'approved' OR approved_at IS NOT NULL",
            name="ck_assignments_approved_requires_timestamp",
        ),
        Index("ix_assignments_complaint", "complaint_id"),
        Index("ix_assignments_approval_status", "approval_status"),
        Index("ix_assignments_department_status", "department_id", "approval_status"),
        Index("ix_assignments_officer", "officer_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ComplaintAssignment approval={self.approval_status.value}>"
