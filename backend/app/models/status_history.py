"""Append-only audit trail of every status transition."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import COMPLAINT_STATUS, ComplaintStatus

if TYPE_CHECKING:
    from app.models.complaint import Complaint


class ComplaintStatusHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable row per status change.

    ``changed_by_agent`` records the agent name when a transition was produced
    by the pipeline; ``changed_by_user_id`` records the officer when a human
    made the decision. Both columns are nullable but at least one is expected
    to be set by the service layer.
    """

    __tablename__ = "complaint_status_history"

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[ComplaintStatus | None] = mapped_column(COMPLAINT_STATUS)
    to_status: Mapped[ComplaintStatus] = mapped_column(COMPLAINT_STATUS, nullable=False)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    changed_by_agent: Mapped[str | None] = mapped_column(String(50))
    reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    complaint: Mapped[Complaint] = relationship(back_populates="status_history")

    __table_args__ = (
        Index("ix_status_history_complaint_changed", "complaint_id", "changed_at"),
        Index("ix_status_history_to_status", "to_status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        previous = self.from_status.value if self.from_status else None
        return f"<StatusHistory {previous} -> {self.to_status.value}>"
