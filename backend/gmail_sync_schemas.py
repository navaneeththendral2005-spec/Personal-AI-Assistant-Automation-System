"""Response schemas for additive Gmail synchronization endpoints."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StoredEmailRead(BaseModel):
    id: int
    gmail_message_id: str
    thread_id: str | None = None
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    message_date: datetime | None = None
    snippet: str | None = None
    labels: list[str]
    synced_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StoredEmailListResponse(BaseModel):
    count: int
    limit: int
    offset: int
    emails: list[StoredEmailRead]
