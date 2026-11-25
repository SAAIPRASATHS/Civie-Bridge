"""SLA breach / escalation recommendations awaiting human approval.

Every row is created with ``requires_human_approval = True``. The escalation
agent can only ever *recommend*; executing an escalation requires an officer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ESCALATION_STATUS,
    ESCALATION_TRIGGER,
    EscalationStatus,
    EscalationTrigger,
)

if TYPE_CHECKING:
    from app.models.complaint import Complaint
    from app.models.department import Department


class EscalationRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A proposed escalation and its approval lifecycle."""

    __tablename__ = "escalation_records"

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False
    )
    escalated_from_department_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL")
    )
    escalated_to_department_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL")
    )
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trigger: Mapped[EscalationTrigger] = mapped_column(
        ESCALATION_TRIGGER, nullable=False, default=EscalationTrigger.SLA_BREACH_PREDICTED
    )
    status: Mapped[EscalationStatus] = mapped_column(
        ESCALATION_STATUS, nullable=False, default=EscalationStatus.RECOMMENDED
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    sla_breach_probability: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))
    predicted_breach_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    complaint: Mapped[Complaint] = relationship()
    escalated_from: Mapped[Department | None] = relationship(
        foreign_keys=[escalated_from_department_id]
    )
    escalated_to: Mapped[Department | None] = relationship(
        foreign_keys=[escalated_to_department_id]
    )

    __table_args__ = (
        UniqueConstraint("complaint_id", "escalation_level", name="uq_escalation_complaint_level"),
        CheckConstraint("escalation_level >= 1", name="ck_escalation_level_positive"),
        CheckConstraint(
            "sla_breach_probability IS NULL "
            "OR (sla_breach_probability >= 0 AND sla_breach_probability <= 1)",
            name="ck_escalation_probability_range",
        ),
        # An approved or executed escalation must record who approved it.
        CheckConstraint(
            "status NOT IN ('approved', 'executed') OR approved_by_user_id IS NOT NULL",
            name="ck_escalation_approved_requires_approver",
        ),
        CheckConstraint(
            "status <> 'executed' OR executed_at IS NOT NULL",
            name="ck_escalation_executed_requires_timestamp",
        ),
        Index("ix_escalations_complaint", "complaint_id"),
        Index("ix_escalations_status", "status"),
        Index("ix_escalations_trigger", "trigger"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<EscalationRecord level={self.escalation_level} status={self.status.value}>"
