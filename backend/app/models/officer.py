"""Officers who can be assigned complaints within a department."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.user import User


class Officer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An officer profile, always linked to exactly one ``users`` row."""

    __tablename__ = "officers"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    employee_code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    designation: Mapped[str | None] = mapped_column(String(120))
    jurisdiction: Mapped[str | None] = mapped_column(String(150))
    max_active_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship(back_populates="officer_profile")
    department: Mapped[Department] = relationship(back_populates="officers")

    __table_args__ = (
        CheckConstraint("max_active_cases > 0", name="ck_officers_max_active_cases_positive"),
        CheckConstraint(
            "length(trim(employee_code)) > 0", name="ck_officers_employee_code_not_blank"
        ),
        Index("ix_officers_department_available", "department_id", "is_available"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Officer {self.employee_code}>"
