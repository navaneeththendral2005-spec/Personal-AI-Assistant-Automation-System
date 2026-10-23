"""Normalize Gmail API messages into fields used by FRIDAY's EmailMessage model."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


def parse_gmail_date(value: str | None) -> datetime | None:
    """Parse a Gmail Date header into a timezone-aware datetime."""
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
    """Map the Gmail service message shape to EmailMessage fields."""
    gmail_id = message.get("gmail_message_id") or message.get("id")

    if not isinstance(gmail_id, str) or not gmail_id.strip():
        raise ValueError("Gmail message is missing a valid message ID.")

    labels = message.get("labels") or []
    if not isinstance(labels, (list, tuple)):
        labels = []

    body_text = message.get("body_text") or ""
    if not isinstance(body_text, str):
        body_text = ""

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
        "body_text": body_text,
        "labels": [str(label) for label in labels],
    }
