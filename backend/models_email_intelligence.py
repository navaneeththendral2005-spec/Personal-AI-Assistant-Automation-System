"""Additive ORM model for FRIDAY's analysis of stored Gmail metadata.

Keep this separate from backend.models so the existing EmailMessage table/model
can remain unchanged. Import this module before Alembic autogenerate if needed.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class EmailAnalysis(Base):
    __tablename__ = "email_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="OTHER")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, index=True, default="NORMAL")
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    classification_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requires_response: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    detected_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deadline_type: Mapped[str] = mapped_column(String(24), nullable=False, default="NONE", server_default="NONE")
    deadline_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    detected_actions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    action_evidence: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    career_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false", index=True)
    career_stage: Mapped[str] = mapped_column(String(24), nullable=False, default="NONE", server_default="NONE", index=True)
    career_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    career_evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete", index=True)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
