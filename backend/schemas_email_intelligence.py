"""Request schemas for the additive email intelligence API."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AskInboxRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question cannot be blank")
        return value


class CreateTaskFromEmailRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    due_at: datetime | None = None

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("due_at must include a timezone offset or Z")
        return value


class EmailIntelligenceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    email_id: int
    gmail_message_id: str
    sender: str | None = None
    subject: str | None = None
    category: str
    priority: str
    priority_score: int
    classification_reason: str
    requires_response: bool
    detected_actions: list[str] = []
    summary: str | None = None
