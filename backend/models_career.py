"""Persistent career opportunity tracking linked to stored Gmail metadata."""
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class CareerOpportunity(Base):
    __tablename__ = "career_opportunities"
    __table_args__ = (UniqueConstraint("email_id", name="uq_career_opportunities_email_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    role_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    opportunity_type: Mapped[str] = mapped_column(String(24), nullable=False, default="JOB", index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DISCOVERED", index=True)
    application_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
