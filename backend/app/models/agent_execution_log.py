"""Per-step execution records for every agent in the LangGraph workflow."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AGENT_EXECUTION_STATUS,
    AGENT_NAME,
    AgentExecutionStatus,
    AgentName,
)

if TYPE_CHECKING:
    from app.models.complaint import Complaint


class AgentExecutionLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Observability record: what each agent received, produced and why.

    ``explanation`` holds the human-readable justification surfaced in the
    officer dashboard, satisfying the explainability requirement without
    duplicating it into the complaint row.
    """

    __tablename__ = "agent_execution_logs"

    complaint_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("complaints.id", ondelete="SET NULL")
    )
    workflow_run_id: Mapped[str | None] = mapped_column(String(64))
    agent_name: Mapped[AgentName] = mapped_column(AGENT_NAME, nullable=False)
    step_index: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[AgentExecutionStatus] = mapped_column(
        AGENT_EXECUTION_STATUS, nullable=False, default=AgentExecutionStatus.STARTED
    )

    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))

    model_name: Mapped[str | None] = mapped_column(String(120))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    complaint: Mapped[Complaint | None] = relationship()

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_agent_logs_confidence_range",
        ),
        CheckConstraint(
            "step_index IS NULL OR step_index >= 0",
            name="ck_agent_logs_step_index_non_negative",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_agent_logs_duration_non_negative",
        ),
        Index("ix_agent_logs_complaint_step", "complaint_id", "step_index"),
        Index("ix_agent_logs_workflow_run", "workflow_run_id"),
        Index("ix_agent_logs_agent_status", "agent_name", "status"),
        Index("ix_agent_logs_created", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AgentExecutionLog {self.agent_name.value} status={self.status.value}>"
