"""Database synchronization for FRIDAY's read-only Gmail integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.models import EmailMessage
from backend.services.email_processor_additive import normalize_gmail_message
from backend.services.gmail_service import list_emails

logger = logging.getLogger("friday.gmail.sync")
MAX_SYNC_BATCH = 50


def _comparable_value(value: Any) -> Any:
    """Canonicalize values where database drivers may change representation."""
    if isinstance(value, datetime):
        # SQLite drops timezone information for DateTime columns, while PostgreSQL
        # preserves it. Treat a naive stored value as UTC to avoid false updates.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, tuple):
        return list(value)
    return value


def sync_gmail_messages(
    db: Session,
    *,
    max_results: int = 10,
    fetcher=None,
) -> dict[str, Any]:
    """Fetch recent Gmail metadata and upsert rows by Gmail message ID.

    A single transaction covers the batch. Any SQLAlchemy database error rolls
    back the whole batch so callers do not receive a misleading partial success.
    `fetcher` is injectable for tests; production defaults to list_emails.
    """
    if not 1 <= max_results <= MAX_SYNC_BATCH:
        raise ValueError(f"max_results must be between 1 and {MAX_SYNC_BATCH}")

    fetch = fetcher or list_emails
    raw_messages = fetch(max_results=max_results)

    inserted = 0
    updated = 0
    unchanged = 0
    now = datetime.now(timezone.utc)

    try:
        for raw_message in raw_messages:
            values = normalize_gmail_message(raw_message)
            existing = db.scalar(
                select(EmailMessage).where(
                    EmailMessage.gmail_message_id == values["gmail_message_id"]
                )
            )
            if existing is None:
                db.add(
                    EmailMessage(
                        **values,
                        synced_at=now,
                    )
                )
                # SessionLocal uses autoflush=False; flush so a duplicate ID
                # later in this same fetched batch can find the pending row.
                db.flush()
                inserted += 1
            else:
                changed_fields = [
                    field
                    for field, value in values.items()
                    if _comparable_value(getattr(existing, field))
                    != _comparable_value(value)
                ]
                if changed_fields:
                    for field in changed_fields:
                        setattr(existing, field, values[field])
                    existing.synced_at = now
                    updated += 1
                else:
                    # Keep synced_at stable when the Gmail metadata did not change.
                    unchanged += 1

        db.commit()
    except (SQLAlchemyError, ValueError):
        db.rollback()
        logger.exception("Gmail synchronization batch failed; transaction rolled back")
        raise

    return {
        "status": "success",
        "fetched": len(raw_messages),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "max_results": max_results,
        "synced_at": now.isoformat(),
        "message": "Gmail metadata synchronized. Gmail messages were not modified.",
    }


def list_stored_emails(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[EmailMessage]:
    """Return stored emails newest first with bounded pagination."""
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    statement = (
        select(EmailMessage)
        .order_by(EmailMessage.message_date.desc().nullslast(), EmailMessage.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all())
