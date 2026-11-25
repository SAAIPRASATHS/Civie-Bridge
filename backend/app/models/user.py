"""Citizen, officer and administrator accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import COMPLAINT_LANGUAGE, USER_ROLE, ComplaintLanguage, UserRole

if TYPE_CHECKING:
    from app.models.complaint import Complaint
    from app.models.officer import Officer


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A platform account. Role determines portal access."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(USER_ROLE, nullable=False, default=UserRole.CITIZEN)
    preferred_language: Mapped[ComplaintLanguage] = mapped_column(
        COMPLAINT_LANGUAGE, nullable=False, default=ComplaintLanguage.EN
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    complaints: Mapped[list[Complaint]] = relationship(
        back_populates="citizen", foreign_keys="Complaint.citizen_id"
    )
    officer_profile: Mapped[Officer | None] = relationship(back_populates="user", uselist=False)

    __table_args__ = (
        Index("ix_users_role_active", "role", "is_active"),
        Index("ix_users_preferred_language", "preferred_language"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} role={self.role.value}>"
