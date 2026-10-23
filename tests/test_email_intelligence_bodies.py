from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage
from backend.models_email_intelligence import EmailAnalysis
from backend.services import email_intelligence_service as intelligence
from backend.services.email_classifier import classify_email


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_email(db, *, subject="Notice", snippet="Short preview", body_text="", sender="hr@example.com"):
    email = EmailMessage(
        gmail_message_id=f"gmail-{subject}-{sender}",
        thread_id="thread-1",
        sender=sender,
        recipient="me@example.com",
        subject=subject,
        message_date=datetime(2026, 10, 20, tzinfo=timezone.utc),
        snippet=snippet,
        body_text=body_text,
        labels=[],
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


def test_local_summary_uses_body_when_available(db_session):
    email = make_email(
        db_session,
        subject="Internship",
        snippet="Short preview only",
        body_text="Applications close on November 3, 2026. Required skill: Python.",
    )

    result = intelligence.summarize_inbox(db_session, limit=10, use_ai=False)

    assert "Applications close on November 3, 2026" in result["summary"]
    assert "Required skill: Python" in result["summary"]
    assert "Short preview only" not in result["summary"]
    assert result["uses_full_bodies"] is True
    assert result["emails_with_body"] == 1
    assert result["attachments_accessed"] is False


def test_summary_falls_back_to_snippet_if_body_missing(db_session):
    make_email(db_session, snippet="Preview fallback", body_text="")

    result = intelligence.summarize_inbox(db_session, use_ai=False)

    assert "Preview fallback" in result["summary"]
    assert result["uses_full_bodies"] is False
    assert result["content_source"] == "snippets_only"


def test_ask_inbox_matches_keywords_only_in_body(db_session, monkeypatch):
    email = make_email(
        db_session,
        subject="An update",
        snippet="A short preview with no unique term",
        body_text="The application deadline is November 3, 2026 for the data science internship.",
    )
    monkeypatch.setattr(intelligence, "_ai_summary", lambda prompt: None)

    result = intelligence.answer_inbox_question(
        db_session, "When is the data science internship deadline?"
    )

    assert result["matched_emails"] == 1
    assert result["uses_full_bodies"] is True
    assert result["evidence"][0]["email_id"] == email.id
    assert result["evidence"][0]["content_source"] == "body_text"
    assert "November 3, 2026" in result["answer"]


def test_ask_inbox_reports_no_matches_instead_of_unrelated_recent_email(db_session, monkeypatch):
    make_email(db_session, subject="Lunch plans", snippet="See you later", body_text="Let's meet for lunch.")
    monkeypatch.setattr(intelligence, "_ai_summary", lambda prompt: None)

    result = intelligence.answer_inbox_question(db_session, "Tell me about quantum cryptography")

    assert result["matched_emails"] == 0
    assert "couldn't find stored emails" in result["answer"]
    assert result["uses_full_bodies"] is False


def test_classifier_uses_body_text_for_job_signals():
    message = type("Message", (), {
        "sender": "hr@example.com",
        "subject": "Update",
        "snippet": "Your message is received",
        "body_text": "We invite you to a technical interview. Please confirm by November 3, 2026.",
        "labels": [],
    })()

    result = classify_email(message)

    assert result["category"] == "JOB"
    assert result["requires_response"] is True
    assert result["detected_deadline"] is not None
    assert result["summary"].startswith("Update: We invite you to a technical interview")



def test_local_ask_returns_concise_evidence_answer_not_raw_inbox_context(db_session, monkeypatch):
    email = make_email(
        db_session,
        subject="Data Science Internship",
        snippet="Opportunity announcement",
        body_text=(
            "We are hiring interns for the data science team. "
            "Applications close on November 3, 2026. "
            "Our company was founded in 2010 and has several offices."
        ),
    )
    monkeypatch.setattr(intelligence, "_ai_summary", lambda prompt: None)

    result = intelligence.answer_inbox_question(
        db_session, "Find internship opportunities and identify application deadlines."
    )

    assert "Data Science Internship" in result["answer"]
    assert "Applications close on November 3, 2026" in result["answer"]
    assert "FRIDAY inbox summary context" not in result["answer"]
    assert len(result["answer"]) < 1200



def test_summary_is_structured_and_not_a_raw_body_dump(db_session, monkeypatch):
    make_email(
        db_session,
        subject="Data Science Internship",
        sender="careers@acme.example",
        snippet="Internship announcement",
        body_text=(
            "We are hiring interns for the data science team. "
            "Eligibility: students currently enrolled in a degree. "
            "Applications close on November 3, 2026. "
            "Read more at https://careers.example/apply. "
            + ("This company has several offices. " * 40)
        ),
    )
    monkeypatch.setattr(intelligence, "_ai_summary", lambda prompt: None)

    result = intelligence.summarize_inbox(db_session, limit=5, use_ai=False)
    summary = result["summary"]

    assert "Internship/job opportunities (1)" in summary
    assert "Applications close on November 3, 2026" in summary
    assert "Eligibility: students currently enrolled in a degree" in summary
    assert "https://careers.example/apply" in summary
    assert "This company has several offices. This company has several offices." not in summary
    assert len(summary) < 2000


def test_date_in_email_is_not_deadline_without_deadline_language(db_session, monkeypatch):
    make_email(
        db_session,
        subject="Internship opportunity",
        snippet="Internship announcement",
        body_text="We are hiring interns. The online information session is November 3, 2026.",
    )
    monkeypatch.setattr(intelligence, "_ai_summary", lambda prompt: None)

    result = intelligence.summarize_inbox(db_session, limit=5, use_ai=False)

    assert "No explicit deadline found" in result["summary"]
    assert "Explicit application deadlines:\n- No explicit application deadlines" in result["summary"]


def test_local_ask_returns_structured_opportunity_details(db_session, monkeypatch):
    make_email(
        db_session,
        subject="Frontend Developer Internship",
        sender="talent@brightlabs.example",
        body_text=(
            "We are hiring interns for a frontend developer internship. "
            "Eligibility: final-year students with JavaScript skills. "
            "Apply by November 3, 2026 at https://brightlabs.example/apply."
        ),
    )
    monkeypatch.setattr(intelligence, "_ai_summary", lambda prompt: None)

    result = intelligence.answer_inbox_question(
        db_session, "Find internship opportunities and application deadlines."
    )

    assert "Frontend Developer Internship" in result["answer"]
    assert "No explicit deadline found" not in result["answer"]
    assert "Apply by November 3, 2026" in result["answer"]
    assert "Eligibility:" in result["answer"]
    assert "https://brightlabs.example/apply" in result["answer"]
