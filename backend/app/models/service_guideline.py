"""Configurable service guidelines backing the resolution agent.

``sla_hours`` is the configurable target response window per category. Every
seeded row carries ``is_verified = False`` and an explicit
``verification_source`` string, so the resolution agent can quote a guideline
while the UI makes clear it is a prototype assumption rather than an officially
published service rule.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import COMPLAINT_CATEGORY, ComplaintCategory

if TYPE_CHECKING:
    from app.models.department import Department


class ServiceGuideline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A retrievable resolution playbook for one category."""

    __tablename__ = "service_guidelines"

    department_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[ComplaintCategory] = mapped_column(COMPLAINT_CATEGORY, nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Configurable SLA target, in hours from complaint submission.
    sla_hours: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ordered resolution playbook and escalation ladder.
    resolution_steps: Mapped[list[Any] | None] = mapped_column(JSONB)
    escalation_matrix: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    required_evidence: Mapped[list[Any] | None] = mapped_column(JSONB)
    applicable_priority: Mapped[str | None] = mapped_column(String(20))
    citizen_response_template: Mapped[str | None] = mapped_column(Text)

    # Provenance: prototype assumptions are explicitly not verified rules.
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_source: Mapped[str | None] = mapped_column(String(255))
    verification_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    department: Mapped[Department] = relationship(back_populates="guidelines")

    __table_args__ = (
        CheckConstraint("sla_hours > 0", name="ck_guidelines_sla_hours_positive"),
        Index("ix_guidelines_category_active", "category", "is_active"),
        Index("ix_guidelines_department", "department_id"),
        Index("ix_guidelines_verified", "is_verified"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ServiceGuideline {self.code} sla_hours={self.sla_hours}>"
