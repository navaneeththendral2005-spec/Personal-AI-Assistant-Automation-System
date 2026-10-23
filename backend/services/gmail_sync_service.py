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
from backend.services.gmail_service import list_emails_page

logger = logging.getLogger("friday.gmail.sync")
MAX_SYNC_BATCH = 500
GMAIL_PAGE_SIZE = 50


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
    """Fetch and upsert up to 500 recent Gmail messages across API pages.

    Gmail requests are kept at 50 messages or fewer per page. The default
    fetcher uses read-only Gmail list/get operations; injected fetchers may
    return a list (legacy one-page test shape), a ``{"emails": ..., 
    "next_page_token": ...}`` mapping, or a ``(emails, next_token)`` tuple.
    A single database transaction covers all fetched pages, and rows are
    upserted by Gmail message ID.
    """
    if not 1 <= max_results <= MAX_SYNC_BATCH:
        raise ValueError(f"max_results must be between 1 and {MAX_SYNC_BATCH}")

    fetch = fetcher
    page_token: str | None = None
    raw_messages: list[dict[str, Any]] = []
    pages_fetched = 0
    remaining = max_results

    while remaining > 0:
        page_size = min(GMAIL_PAGE_SIZE, remaining)
        if fetch is None:
            page = list_emails_page(max_results=page_size, page_token=page_token)
        else:
            page = fetch(max_results=page_size, page_token=page_token)

        # Keep compatibility with existing injected test fetchers that return
        # a plain list. Such a fetcher represents a single page unless it opts
        # into the explicit continuation-token shape.
        if isinstance(page, dict):
            page_messages = page.get("emails", page.get("messages", []))
            next_token = page.get("next_page_token", page.get("nextPageToken"))
        elif isinstance(page, tuple) and len(page) == 2:
            page_messages, next_token = page
        else:
            page_messages, next_token = page, None

        if not isinstance(page_messages, list):
            raise ValueError("Gmail page fetcher must return a list of messages")
        page_messages = page_messages[:page_size]
        raw_messages.extend(page_messages)
        pages_fetched += 1
        remaining -= len(page_messages)

        # Stop when Gmail has no next page, an empty page is returned, or a
        # legacy list-only fetcher was used. Never loop forever on a bad token.
        if not next_token or not page_messages:
            break
        page_token = str(next_token)

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
                # later in this same sync can find the pending row.
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
                    unchanged += 1

        db.commit()
    except (SQLAlchemyError, ValueError):
        db.rollback()
        logger.exception("Gmail synchronization failed; transaction rolled back")
        raise

    return {
        "status": "success",
        "fetched": len(raw_messages),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "max_results": max_results,
        "pages_fetched": pages_fetched,
        "synced_at": now.isoformat(),
        "message": "Gmail messages synchronized using read-only API calls. Gmail messages were not modified.",
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
