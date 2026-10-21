"""Normalize Gmail API metadata into fields used by FRIDAY's EmailMessage model."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


def parse_gmail_date(value: str | None) -> datetime | None:
    """Parse a RFC 2822 Gmail Date header into a timezone-aware datetime.

    Invalid or missing headers return None rather than aborting a sync batch.
    Naive parsed dates are treated as UTC because the header did not provide
    timezone information.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_gmail_message(message: dict[str, Any]) -> dict[str, Any]:
    """Map the current gmail_service.list_emails() shape to EmailMessage fields."""
    gmail_id = message.get("gmail_message_id") or message.get("id")
    if not isinstance(gmail_id, str) or not gmail_id.strip():
        raise ValueError("Gmail message is missing a valid message ID.")

    labels = message.get("labels") or []
    if not isinstance(labels, (list, tuple)):
        labels = []

    return {
        "gmail_message_id": gmail_id.strip(),
        "thread_id": message.get("thread_id"),
        "sender": message.get("sender"),
        "recipient": message.get("recipient"),
        "subject": message.get("subject"),
        "message_date": parse_gmail_date(
            message.get("message_date") or message.get("date")
        ),
        "snippet": message.get("snippet") or "",
        "labels": [str(label) for label in labels],
    }
