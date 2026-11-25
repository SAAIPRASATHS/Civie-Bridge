"""Municipal departments that own complaint categories.

``is_prototype_data`` is True for every row seeded in this prototype. No row in
this table represents a verified real government assignment, and the routing
agent must only ever select departments read from this table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import COMPLAINT_CATEGORY, ComplaintCategory

if TYPE_CHECKING:
    from app.models.complaint import Complaint
    from app.models.officer import Officer
    from app.models.service_guideline import ServiceGuideline


class Department(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A department in the controlled routing directory."""

    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[ComplaintCategory] = mapped_column(COMPLAINT_CATEGORY, nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    office_address: Mapped[str | None] = mapped_column(String(255))
    jurisdiction: Mapped[str | None] = mapped_column(String(150))

    # Provenance: the routing agent must never invent a department, so every
    # directory row carries an explicit statement of where it came from.
    is_prototype_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    data_source: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    officers: Mapped[list[Officer]] = relationship(back_populates="department")
    guidelines: Mapped[list[ServiceGuideline]] = relationship(back_populates="department")
    complaints: Mapped[list[Complaint]] = relationship(
        back_populates="current_department", foreign_keys="Complaint.current_department_id"
    )

    __table_args__ = (
        Index("ix_departments_category_active", "category", "is_active"),
        Index("ix_departments_prototype", "is_prototype_data"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Department {self.code} category={self.category.value}>"
