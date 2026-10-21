from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage
from backend.services.email_classifier import classify_email
from backend.services.email_intelligence_service import analyze_stored_emails, list_analyzed_emails


def message(sender, subject, snippet, labels=None):
    return SimpleNamespace(sender=sender, subject=subject, snippet=snippet, labels=labels or [])


def test_opportunity_alert_gets_career_signal_without_inventing_deadline_or_action():
    result = classify_email(message(
        "ISP Team from Internshala <student@mail.internshala.com>",
        "Update: A new opportunity is available for you | 1 new alert",
        "Your next college opportunity is here! Have you thought about bringing opportunities to other students?",
        ["CATEGORY_UPDATES", "INBOX"],
    ))
    assert result["category"] in {"JOB", "UPDATES"}
    assert result["career_signal"] is True
    assert result["career_stage"] == "OPPORTUNITY"
    assert result["career_confidence"] >= 80
    assert result["detected_deadline"] is None
    assert result["deadline_type"] == "NONE"
    assert result["detected_actions"] == []
    assert result["action_evidence"] == []
    assert result["career_evidence"]


def test_explicit_application_and_interview_have_distinct_stages():
    application = classify_email(message("careers@example.com", "Internship application deadline", "Submit your application by 2026-11-03"))
    interview = classify_email(message("recruiter@example.com", "Interview invitation", "Please confirm your technical interview on Friday."))
    assert application["career_stage"] == "APPLICATION"
    assert application["detected_deadline"] is not None
    assert any(x["action"] == "APPLY_FOR_JOB" for x in application["action_evidence"])
    assert interview["career_stage"] == "INTERVIEW"
    assert any(x["action"] == "CONFIRM" for x in interview["action_evidence"])


def test_analysis_persists_and_filters_career_evidence():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        session.add(EmailMessage(
            gmail_message_id="career-alert", thread_id="t1", sender="Internshala <student@internshala.com>",
            recipient="me@example.com", subject="New internship opportunity", message_date=datetime.now(timezone.utc),
            snippet="Explore this internship opportunity.", labels=["INBOX"],
        ))
        session.add(EmailMessage(
            gmail_message_id="other", thread_id="t2", sender="friend@example.com", recipient="me@example.com",
            subject="Hello", message_date=datetime.now(timezone.utc), snippet="Just checking in.", labels=["INBOX"],
        ))
        session.commit()
        result = analyze_stored_emails(session)
        assert result["created"] == 2
        filtered = list_analyzed_emails(session, career_only=True)
        assert filtered["count"] == 1
        item = filtered["emails"][0]
        assert item["career_signal"] is True
        assert item["career_stage"] == "OPPORTUNITY"
        assert item["career_evidence"]
        assert item["action_evidence"] == []
        assert list_analyzed_emails(session, has_deadline=True)["count"] == 0
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
