"""Tests for private, resumable email labeling using only in-memory SQLite."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import EmailMessage
from scripts.export_email_dataset import COLUMNS, export_email_dataset
from scripts.label_emails import label_dataset, load_rows


def make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, factory


def add_email(factory, *, gmail_id="fake-1", body="Synthetic message body"):
    db = factory()
    try:
        db.add(EmailMessage(
            gmail_message_id=gmail_id,
            thread_id="thread-1",
            sender="sender@example.test",
            recipient="recipient@example.test",
            subject="Synthetic subject",
            message_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            snippet="Synthetic snippet",
            body_text=body,
            labels=["INBOX"],
        ))
        db.commit()
    finally:
        db.close()


def read_csv(path: Path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_export_includes_target_labels_and_preserves_existing_human_labels(tmp_path):
    engine, factory = make_session_factory()
    try:
        add_email(factory)
        output = tmp_path / "data" / "email_labels.csv"
        output.parent.mkdir()
        with output.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["email_id", "true_priority", "true_needs_response", "true_category"])
            writer.writeheader()
            writer.writerow({"email_id": "fake-1", "true_priority": "NEEDS_REPLY", "true_needs_response": "yes", "true_category": "JOB"})

        assert export_email_dataset(output, limit=10, session_factory=factory) == 1
        rows = read_csv(output)
        assert rows[0]["email_id"] == "fake-1"
        assert rows[0]["true_priority"] == "NEEDS_REPLY"
        assert rows[0]["true_needs_response"] == "yes"
        assert rows[0]["true_category"] == "JOB"
        assert rows[0]["body_text"] == "Synthetic message body"
        assert tuple(rows[0].keys()) == COLUMNS

        # A row outside the current export window remains available for resuming.
        with output.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writerow({"email_id": "older-labeled-email", "sender": "old@example.test", "subject": "Older", "true_priority": "INFORMATIONAL", "true_needs_response": "no", "true_category": "UPDATES"})
        assert export_email_dataset(output, limit=1, session_factory=factory) == 1
        refreshed = read_csv(output)
        assert {row["email_id"] for row in refreshed} == {"fake-1", "older-labeled-email"}
        older = next(row for row in refreshed if row["email_id"] == "older-labeled-email")
        assert older["true_priority"] == "INFORMATIONAL"
    finally:
        engine.dispose()


def test_labeler_saves_each_email_and_resumes_by_skipping_complete_rows(tmp_path):
    path = tmp_path / "email_labels.csv"
    path.write_text(
        "email_id,sender,subject,snippet,body_text,labels\n"
        "fake-1,sender@example.test,Synthetic,Snippet,This is a synthetic body,INBOX\n",
        encoding="utf-8",
    )
    answers = iter(["URGENT", "yes", "JOB"])
    completed, total = label_dataset(path, input_fn=lambda _prompt: next(answers), output=io.StringIO(), body_chars=8)
    assert (completed, total) == (1, 1)
    rows = read_csv(path)
    assert rows[0]["true_priority"] == "URGENT"
    assert rows[0]["true_needs_response"] == "yes"
    assert rows[0]["true_category"] == "JOB"

    # Fully labeled rows are skipped, so a second session needs no input.
    completed_again, total_again = label_dataset(path, input_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("should skip completed row")), output=io.StringIO())
    assert (completed_again, total_again) == (0, 1)


def test_quit_saves_partial_progress_and_body_is_truncated(tmp_path):
    path = tmp_path / "email_labels.csv"
    path.write_text(
        "email_id,sender,subject,snippet,body_text,labels\n"
        "fake-1,sender@example.test,Synthetic,Snippet,1234567890abcdefgh,INBOX\n",
        encoding="utf-8",
    )
    answers = iter(["URGENT", "q"])
    output = io.StringIO()
    completed, total = label_dataset(path, input_fn=lambda _prompt: next(answers), output=output, body_chars=5)
    assert completed == 0
    assert total == 0
    assert "12345… [truncated]" in output.getvalue()
    row = read_csv(path)[0]
    assert row["true_priority"] == "URGENT"
    assert row["true_needs_response"] == ""


def test_load_rows_adds_missing_label_columns_without_losing_existing_data(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("email_id,subject\nfake-1,Synthetic\n", encoding="utf-8")
    columns, rows = load_rows(path)
    assert all(field in columns for field in ("true_priority", "true_needs_response", "true_category"))
    assert rows[0]["email_id"] == "fake-1"
    assert rows[0]["true_category"] == ""
