"""The central complaint record."""

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
    COMPLAINT_CATEGORY,
    COMPLAINT_LANGUAGE,
    COMPLAINT_PRIORITY,
    COMPLAINT_STATUS,
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    ComplaintStatus,
)

if TYPE_CHECKING:
    from app.models.assignment import ComplaintAssignment
    from app.models.complaint_evidence import ComplaintEvidence
    from app.models.department import Department
    from app.models.status_history import ComplaintStatusHistory
    from app.models.user import User


class Complaint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A citizen grievance and everything the agent pipeline recorded about it.

    AI-produced values live in dedicated columns (``classification_*``,
    ``duplicate_*``, ``urgency_score``) so that an agent's output is always
    distinguishable from a human-entered or citizen-supplied fact.
    """

    __tablename__ = "complaints"

    # -- Identity ----------------------------------------------------------
    reference_id: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    citizen_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    # -- Citizen submission ------------------------------------------------
    title: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[ComplaintLanguage] = mapped_column(
        COMPLAINT_LANGUAGE, nullable=False, default=ComplaintLanguage.EN
    )
    location_text: Mapped[str | None] = mapped_column(String(255))
    ward: Mapped[str | None] = mapped_column(String(80))
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6, asdecimal=False))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6, asdecimal=False))
    location_accuracy_m: Mapped[int | None] = mapped_column(Integer)

    # -- Lifecycle ---------------------------------------------------------
    status: Mapped[ComplaintStatus] = mapped_column(
        COMPLAINT_STATUS, nullable=False, default=ComplaintStatus.SUBMITTED
    )
    category: Mapped[ComplaintCategory | None] = mapped_column(COMPLAINT_CATEGORY)
    priority: Mapped[ComplaintPriority | None] = mapped_column(COMPLAINT_PRIORITY)
    current_department_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL")
    )

    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # -- Agent findings ----------------------------------------------------
    classification_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))
    classification_explanation: Mapped[str | None] = mapped_column(Text)
    urgency_score: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))
    needs_clarification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    clarification_question: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("complaints.id", ondelete="SET NULL")
    )
    duplicate_similarity: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))

    # -- Provenance --------------------------------------------------------
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # -- Relationships -----------------------------------------------------
    citizen: Mapped[User | None] = relationship(
        back_populates="complaints", foreign_keys=[citizen_id]
    )
    current_department: Mapped[Department | None] = relationship(
        back_populates="complaints", foreign_keys=[current_department_id]
    )
    duplicate_of: Mapped[Complaint | None] = relationship(
        remote_side="Complaint.id", foreign_keys=[duplicate_of_id]
    )
    evidence: Mapped[list[ComplaintEvidence]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[ComplaintAssignment]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan"
    )
    status_history: Mapped[list[ComplaintStatusHistory]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Coordinates are optional, but half a coordinate pair is a data error.
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) "
            "OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_complaints_coordinates_paired",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_complaints_latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_complaints_longitude_range",
        ),
        CheckConstraint(
            "classification_confidence IS NULL "
            "OR (classification_confidence >= 0 AND classification_confidence <= 1)",
            name="ck_complaints_classification_confidence_range",
        ),
        CheckConstraint(
            "duplicate_similarity IS NULL OR (duplicate_similarity >= 0 AND duplicate_similarity <= 1)",
            name="ck_complaints_duplicate_similarity_range",
        ),
        CheckConstraint(
            "urgency_score IS NULL OR (urgency_score >= 0 AND urgency_score <= 1)",
            name="ck_complaints_urgency_score_range",
        ),
        # A complaint cannot be a duplicate of itself.
        CheckConstraint(
            "duplicate_of_id IS NULL OR duplicate_of_id <> id",
            name="ck_complaints_not_self_duplicate",
        ),
        # Terminal states carry their timestamps.
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= submitted_at",
            name="ck_complaints_resolved_after_submitted",
        ),
        Index("ix_complaints_status_reported", "status", "reported_at"),
        Index("ix_complaints_category_status", "category", "status"),
        Index("ix_complaints_priority_status", "priority", "status"),
        Index("ix_complaints_ward", "ward"),
        Index("ix_complaints_department", "current_department_id"),
        Index("ix_complaints_citizen", "citizen_id"),
        Index("ix_complaints_sla_due", "sla_due_at"),
        Index("ix_complaints_duplicate_of", "duplicate_of_id"),
        Index("ix_complaints_language", "language"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Complaint {self.reference_id} status={self.status.value}>"
