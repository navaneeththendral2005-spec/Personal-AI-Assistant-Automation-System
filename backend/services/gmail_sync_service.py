"""Database synchronization for FRIDAY's read-only Gmail integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.models import EmailMessage, GmailThreadSyncState
from backend.services.email_processor_additive import normalize_gmail_message
from backend.services.gmail_service import get_gmail_service, get_thread_messages, list_emails_page

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


def _upsert_raw_messages(db: Session, raw_messages: list[dict[str, Any]], now: datetime) -> tuple[int, int, int]:
    inserted = updated = unchanged = 0
    for raw_message in raw_messages:
        values = normalize_gmail_message(raw_message)
        existing = db.scalar(select(EmailMessage).where(
            EmailMessage.gmail_message_id == values["gmail_message_id"]
        ))
        if existing is None:
            db.add(EmailMessage(**values, synced_at=now))
            db.flush()
            inserted += 1
            continue
        changed_fields = [
            field for field, value in values.items()
            if _comparable_value(getattr(existing, field)) != _comparable_value(value)
        ]
        if changed_fields:
            for field in changed_fields:
                setattr(existing, field, values[field])
            existing.synced_at = now
            updated += 1
        else:
            unchanged += 1
    return inserted, updated, unchanged


def _record_verified_thread(
    db: Session, thread_id: str, message_count: int, now: datetime
) -> None:
    state = db.scalar(select(GmailThreadSyncState).where(
        GmailThreadSyncState.gmail_thread_id == thread_id
    ))
    if state is None:
        state = GmailThreadSyncState(gmail_thread_id=thread_id)
        db.add(state)
    state.sync_status = "VERIFIED"
    state.message_count = message_count
    state.last_synced_at = now
    state.updated_at = now
    state.last_error_type = None


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
            page = list_emails_page(
                max_results=page_size,
                page_token=page_token,
                include_thread_history=True,
            )
        else:
            page = fetch(max_results=page_size, page_token=page_token)

        # Keep compatibility with existing injected test fetchers that return
        # a plain list. Such a fetcher represents a single page unless it opts
        # into the explicit continuation-token shape.
        listed_count = None
        expanded_history = False
        if isinstance(page, dict):
            page_messages = page.get("emails", page.get("messages", []))
            next_token = page.get("next_page_token", page.get("nextPageToken"))
            listed_count = page.get("listed_count")
            expanded_history = bool(page.get("expanded_thread_history"))
        elif isinstance(page, tuple) and len(page) == 2:
            page_messages, next_token = page
        else:
            page_messages, next_token = page, None

        if not isinstance(page_messages, list):
            raise ValueError("Gmail page fetcher must return a list of messages")
        if not expanded_history:
            page_messages = page_messages[:page_size]
        raw_messages.extend(page_messages)
        pages_fetched += 1
        # max_results bounds the number of recent message IDs requested from
        # Gmail, while expanded thread history may add older messages to store.
        remaining -= int(listed_count if listed_count is not None else len(page_messages))

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
        inserted, updated, unchanged = _upsert_raw_messages(db, raw_messages, now)
        if fetch is None:
            thread_counts: dict[str, int] = {}
            for item in raw_messages:
                thread_id = str(item.get("thread_id") or "").strip()
                if thread_id:
                    thread_counts[thread_id] = thread_counts.get(thread_id, 0) + 1
            for thread_id, message_count in thread_counts.items():
                _record_verified_thread(db, thread_id, message_count, now)
        db.commit()
    except Exception as exc:
        # A malformed provider payload or an unexpected normalization bug must
        # roll back the entire multi-page batch just like a database failure.
        # Log only the exception type: provider exceptions can contain message data.
        db.rollback()
        logger.error(
            "Gmail synchronization failed; transaction rolled back | error_type=%s",
            type(exc).__name__,
        )
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
        "thread_history_expanded": fetch is None,
        "message": (
            "Gmail messages and their available conversation history synchronized using read-only API calls. "
            "Gmail messages were not modified."
            if fetch is None else
            "Gmail messages synchronized using read-only API calls. Gmail messages were not modified."
        ),
    }


def sync_stored_thread_histories(
    db: Session,
    *,
    max_threads: int = 50,
    service=None,
    thread_fetcher=None,
) -> dict[str, Any]:
    """Backfill complete histories for locally known Gmail thread IDs.

    Successful threads are checkpointed so later sync runs continue with the
    remaining threads instead of repeatedly fetching the same history. Only
    read-only Gmail ``threads.get`` requests are used. Coverage is deliberately
    scoped to thread IDs already present in FRIDAY's local database; it does not
    claim a full mailbox-wide audit.
    """
    if not 1 <= max_threads <= 500:
        raise ValueError("max_threads must be between 1 and 500")

    thread_ids = [value for value in db.query(EmailMessage.thread_id)
                  .filter(EmailMessage.thread_id.isnot(None))
                  .distinct().all() if value and str(value[0]).strip()]
    local_ids = sorted({str(value[0]).strip() for value in thread_ids})
    states = {state.gmail_thread_id: state for state in db.query(GmailThreadSyncState).all()}
    candidates = [thread_id for thread_id in local_ids
                  if thread_id not in states or states[thread_id].sync_status != "VERIFIED"]
    # Never let a repeatedly failing thread starve never-attempted threads.
    candidates.sort(key=lambda thread_id: (
        states[thread_id].updated_at if thread_id in states and states[thread_id].updated_at else datetime.min.replace(tzinfo=timezone.utc),
        thread_id,
    ))
    selected = candidates[:max_threads]

    if selected and service is None and thread_fetcher is None:
        service = get_gmail_service()

    examined = verified = failed = fetched = inserted = updated = unchanged = 0
    for thread_id in selected:
        examined += 1
        try:
            messages = (thread_fetcher(thread_id) if thread_fetcher else
                        get_thread_messages(thread_id, service=service))
            if not isinstance(messages, list):
                raise ValueError("Thread fetcher must return a list of messages")
            now = datetime.now(timezone.utc)
            i, u, n = _upsert_raw_messages(db, messages, now)
            inserted += i; updated += u; unchanged += n; fetched += len(messages)
            _record_verified_thread(db, thread_id, len(messages), now)
            db.commit()
            verified += 1
        except Exception as exc:
            db.rollback()
            # Persist only the exception class, never provider text or message data.
            now = datetime.now(timezone.utc)
            state = db.scalar(select(GmailThreadSyncState).where(
                GmailThreadSyncState.gmail_thread_id == thread_id
            ))
            if state is None:
                state = GmailThreadSyncState(gmail_thread_id=thread_id)
                db.add(state)
            state.sync_status = "FAILED"
            state.updated_at = now
            state.last_error_type = type(exc).__name__
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            failed += 1
            logger.warning("Gmail thread history sync failed | error_type=%s", type(exc).__name__)

    # Refresh counts after successful/failed checkpoints.
    states = {state.gmail_thread_id: state for state in db.query(GmailThreadSyncState).all()}
    verified_local = sum(1 for thread_id in local_ids
                         if thread_id in states and states[thread_id].sync_status == "VERIFIED")
    remaining = max(0, len(local_ids) - verified_local)
    return {
        "status": "success" if failed == 0 else "partial",
        "local_thread_count": len(local_ids),
        "threads_examined": examined,
        "threads_verified_this_run": verified,
        "threads_failed_this_run": failed,
        "verified_local_thread_count": verified_local,
        "remaining_unverified_threads": remaining,
        "messages_fetched": fetched,
        "messages_inserted": inserted,
        "messages_updated": updated,
        "messages_unchanged": unchanged,
        "coverage_status": "STORED_THREADS_VERIFIED" if remaining == 0 else "PARTIAL_LOCAL_HISTORY",
        "mailbox_wide_guarantee": False,
        "message": (
            "All locally known Gmail thread IDs have been fetched successfully; this does not establish that every mailbox thread is stored."
            if remaining == 0 else
            "Historical thread verification is checkpointed. Run synchronization again to continue remaining local thread IDs."
        ),
        "write_actions_taken": False,
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
