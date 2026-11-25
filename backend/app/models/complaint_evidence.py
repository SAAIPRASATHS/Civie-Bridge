"""Uploaded photo evidence and the outcome of AI verification."""

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
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    EVIDENCE_VERIFICATION_STATUS,
    EvidenceVerificationStatus,
)

if TYPE_CHECKING:
    from app.models.complaint import Complaint


class ComplaintEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single evidence file attached to a complaint.

    ``verification_status`` starts as ``pending``. The evidence agent may only
    move it to ``verified_relevant`` / ``uncertain`` / ``not_relevant``; the
    ``manual_review_required`` state is reserved for human escalation.
    """

    __tablename__ = "complaint_evidence"

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    caption: Mapped[str | None] = mapped_column(Text)

    verification_status: Mapped[EvidenceVerificationStatus] = mapped_column(
        EVIDENCE_VERIFICATION_STATUS,
        nullable=False,
        default=EvidenceVerificationStatus.PENDING,
    )
    relevance_score: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_notes: Mapped[str | None] = mapped_column(Text)
    agent_explanation: Mapped[str | None] = mapped_column(Text)
    analysis_model: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    complaint: Mapped[Complaint] = relationship(back_populates="evidence")

    __table_args__ = (
        CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 1)",
            name="ck_evidence_relevance_score_range",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes > 0",
            name="ck_evidence_file_size_positive",
        ),
        Index("ix_evidence_complaint", "complaint_id"),
        Index("ix_evidence_verification_status", "verification_status"),
        Index("ix_evidence_manual_review", "requires_manual_review"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ComplaintEvidence {self.file_name} status={self.verification_status.value}>"
