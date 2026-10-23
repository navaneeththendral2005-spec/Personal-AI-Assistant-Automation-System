from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage
from backend.services.email_processor_additive import (
    normalize_gmail_message,
    parse_gmail_date,
)
from backend.services.gmail_sync_service import sync_gmail_messages


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    session = TestingSession()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_parse_date_is_timezone_aware():
    parsed = parse_gmail_date("Tue, 20 Oct 2026 10:54:57 +0000")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_invalid_date_returns_none():
    assert parse_gmail_date("not a date") is None
    assert parse_gmail_date(None) is None


def test_normalize_current_gmail_service_shape():
    result = normalize_gmail_message({
        "id": "gmail-123",
        "thread_id": "thread-1",
        "sender": "sender@example.com",
        "recipient": "me@example.com",
        "subject": "Hello",
        "date": "Tue, 20 Oct 2026 10:54:57 +0000",
        "snippet": "Preview",
        "body_text": "This is the full email body.",
        "labels": ["INBOX"],
    })

    assert result["gmail_message_id"] == "gmail-123"
    assert result["message_date"].tzinfo is not None
    assert result["labels"] == ["INBOX"]
    assert result["body_text"] == "This is the full email body."


def test_normalize_rejects_missing_message_id():
    with pytest.raises(ValueError):
        normalize_gmail_message({"subject": "No ID"})


def test_sync_inserts_then_updates_without_duplicates(db_session):
    messages = [{
        "id": "gmail-123",
        "thread_id": "thread-1",
        "sender": "sender@example.com",
        "recipient": "me@example.com",
        "subject": "Original",
        "date": "Tue, 20 Oct 2026 10:54:57 +0000",
        "snippet": "Preview",
        "body_text": "Original email body.",
        "labels": ["INBOX"],
    }]

    first = sync_gmail_messages(
        db_session,
        max_results=10,
        fetcher=lambda **kwargs: messages,
    )

    assert first["inserted"] == 1
    assert first["updated"] == 0

    row = db_session.scalar(
        select(EmailMessage).where(
            EmailMessage.gmail_message_id == "gmail-123"
        )
    )

    assert row is not None
    assert row.body_text == "Original email body."

    messages[0]["subject"] = "Updated"
    messages[0]["body_text"] = "Updated email body."

    second = sync_gmail_messages(
        db_session,
        max_results=10,
        fetcher=lambda **kwargs: messages,
    )

    assert second["inserted"] == 0
    assert second["updated"] == 1

    rows = list(db_session.scalars(select(EmailMessage)).all())

    assert len(rows) == 1
    assert rows[0].subject == "Updated"
    assert rows[0].body_text == "Updated email body."


def test_sync_identical_message_is_unchanged_and_keeps_synced_at(db_session):
    messages = [{
        "id": "gmail-same",
        "thread_id": "thread-1",
        "sender": "sender@example.com",
        "recipient": "me@example.com",
        "subject": "Same message",
        "date": "Tue, 20 Oct 2026 10:54:57 +0000",
        "snippet": "Preview",
        "body_text": "Same email body.",
        "labels": ["INBOX"],
    }]

    first = sync_gmail_messages(
        db_session,
        max_results=10,
        fetcher=lambda **kwargs: messages,
    )

    assert first["inserted"] == 1
    assert first["updated"] == 0
    assert first["unchanged"] == 0

    row_before = db_session.scalar(
        select(EmailMessage).where(
            EmailMessage.gmail_message_id == "gmail-same"
        )
    )

    assert row_before is not None
    assert row_before.body_text == "Same email body."

    original_synced_at = row_before.synced_at

    second = sync_gmail_messages(
        db_session,
        max_results=10,
        fetcher=lambda **kwargs: messages,
    )

    assert second["inserted"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 1

    row_after = db_session.scalar(
        select(EmailMessage).where(
            EmailMessage.gmail_message_id == "gmail-same"
        )
    )

    assert row_after is not None
    assert row_after.body_text == "Same email body."
    assert row_after.synced_at == original_synced_at

    rows = list(db_session.scalars(select(EmailMessage)).all())
    assert len(rows) == 1


def test_duplicate_identical_messages_in_same_batch_count_unchanged(
    db_session,
):
    message = {
        "id": "gmail-duplicate",
        "thread_id": "thread-1",
        "sender": "sender@example.com",
        "recipient": "me@example.com",
        "subject": "Duplicate in batch",
        "date": "Tue, 20 Oct 2026 10:54:57 +0000",
        "snippet": "Preview",
        "body_text": "Duplicate test body.",
        "labels": ["INBOX"],
    }

    result = sync_gmail_messages(
        db_session,
        max_results=10,
        fetcher=lambda **kwargs: [message, message.copy()],
    )

    assert result["fetched"] == 2
    assert result["inserted"] == 1
    assert result["updated"] == 0
    assert result["unchanged"] == 1

    rows = list(db_session.scalars(select(EmailMessage)).all())
    assert len(rows) == 1
    assert rows[0].body_text == "Duplicate test body."


def test_sync_batch_size_validation(db_session):
    with pytest.raises(ValueError):
        sync_gmail_messages(
            db_session,
            max_results=501,
            fetcher=lambda **kwargs: [],
        )


def _fake_email(message_id: str) -> dict:
    return {
        "id": message_id,
        "thread_id": f"thread-{message_id}",
        "sender": "synthetic@example.test",
        "recipient": "owner@example.test",
        "subject": f"Synthetic message {message_id}",
        "date": "Tue, 20 Oct 2026 10:54:57 +0000",
        "snippet": "Synthetic test preview",
        "body_text": f"Synthetic body for {message_id}",
        "labels": ["INBOX"],
    }


def test_syncs_300_messages_across_pages_and_rerun_is_idempotent(db_session):
    messages = [_fake_email(f"fake-{index:03d}") for index in range(300)]
    def fake_paged_fetcher(*, max_results, page_token=None):
        start = int(page_token or 0)
        page = messages[start : start + max_results]
        next_start = start + len(page)
        next_token = str(next_start) if next_start < len(messages) else None
        return {"emails": page, "next_page_token": next_token}

    first = sync_gmail_messages(
        db_session,
        max_results=300,
        fetcher=fake_paged_fetcher,
    )

    assert first["fetched"] == 300
    assert first["inserted"] == 300
    assert first["updated"] == 0
    assert first["unchanged"] == 0
    assert first["pages_fetched"] == 6
    assert len(list(db_session.scalars(select(EmailMessage)).all())) == 300

    second = sync_gmail_messages(
        db_session,
        max_results=300,
        fetcher=fake_paged_fetcher,
    )

    assert second["fetched"] == 300
    assert second["inserted"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 300
    assert second["pages_fetched"] == 6
    assert len(list(db_session.scalars(select(EmailMessage)).all())) == 300


def test_sync_accepts_maximum_of_500_and_rejects_more(db_session):
    assert sync_gmail_messages(
        db_session,
        max_results=500,
        fetcher=lambda **kwargs: [],
    )["fetched"] == 0
    with pytest.raises(ValueError, match="1 and 500"):
        sync_gmail_messages(
            db_session,
            max_results=501,
            fetcher=lambda **kwargs: [],
        )
