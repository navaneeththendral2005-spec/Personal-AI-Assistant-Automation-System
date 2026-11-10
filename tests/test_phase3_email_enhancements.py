from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage
from backend.models_email_intelligence import EmailAnalysis
from backend.services.email_intelligence_service import answer_inbox_question, summarize_inbox
from backend.services.email_response_tracking_service import list_unanswered_threads
from backend.services.assistant_query_service import answer_assistant_question


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def add_email(db, *, gmail_id, thread, subject, sender, recipient="me@example.com", body="", labels=None, day=1):
    email = EmailMessage(
        gmail_message_id=gmail_id, thread_id=thread, subject=subject, sender=sender,
        recipient=recipient, snippet=body[:120], body_text=body, labels=labels or [],
        message_date=datetime(2026, 11, day, 10, tzinfo=timezone.utc),
    )
    db.add(email)
    db.flush()
    return email


def test_summary_exposes_structured_per_email_intelligence(db):
    email = add_email(db, gmail_id="sum1", thread="tsum", subject="Interview invitation", sender="hr@example.com",
                      body="Please confirm your technical interview by November 12, 2026.", day=2)
    db.add(EmailAnalysis(email_id=email.id, category="JOB", priority="HIGH", priority_score=80,
                         requires_response=True, response_confidence=85,
                         detected_actions=["CONFIRM"], summary="Interview invitation"))
    db.commit()
    result = summarize_inbox(db, use_ai=False)
    assert result["read_only"] is True
    assert result["email_summaries"][0]["email_id"] == email.id
    assert result["email_summaries"][0]["priority"] == "HIGH"
    assert result["email_summaries"][0]["detected_actions"] == ["CONFIRM"]


def test_natural_language_search_ranks_subject_and_body_evidence(db, monkeypatch):
    from backend.services import email_intelligence_service as service
    monkeypatch.setattr(service, "_ai_summary", lambda prompt: None)
    weak = add_email(db, gmail_id="weak", thread="tw", subject="General update", sender="x@example.com",
                     body="A general update for you.", day=2)
    strong = add_email(db, gmail_id="strong", thread="ts", subject="Technical assessment invitation", sender="hr@example.com",
                       body="Please complete the Python technical assessment by November 12.", day=1)
    db.commit()
    result = answer_inbox_question(db, "Find the Python technical assessment invitation")
    assert result["matched_emails"] == 1
    assert result["evidence"][0]["email_id"] == strong.id
    assert result["evidence"][0]["relevance_score"] > 0


def test_unanswered_thread_detection_excludes_replied_conversations(db):
    incoming = add_email(db, gmail_id="in1", thread="pending-thread", subject="Please confirm interview",
                         sender="recruiter@example.com", body="Please reply to confirm your interview.", day=1)
    replied_in = add_email(db, gmail_id="in2", thread="replied-thread", subject="Can we schedule?",
                           sender="recruiter@example.com", body="Please let me know your availability.", day=1)
    replied_out = add_email(db, gmail_id="out2", thread="replied-thread", subject="Re: Can we schedule?",
                            sender="me@example.com", recipient="recruiter@example.com", body="I am available.", labels=["SENT"], day=2)
    db.add_all([
        EmailAnalysis(email_id=incoming.id, category="JOB", priority="HIGH", priority_score=80,
                      requires_response=True, response_confidence=90),
        EmailAnalysis(email_id=replied_in.id, category="OTHER", priority="NORMAL", priority_score=30,
                      requires_response=True, response_confidence=80),
        EmailAnalysis(email_id=replied_out.id, category="OTHER", priority="NORMAL", priority_score=10,
                      requires_response=False, response_confidence=0),
    ])
    db.commit()
    result = list_unanswered_threads(db, now=datetime(2026, 11, 3, 10, tzinfo=timezone.utc))
    assert result["total_unanswered_threads"] == 1
    assert result["threads"][0]["thread_id"] == "pending-thread"
    assert result["complete_thread_guarantee"] is False
    assert result["write_actions_taken"] is False


def test_unanswered_natural_language_query_uses_thread_service(db):
    email = add_email(db, gmail_id="wait1", thread="wait-thread", subject="Interview confirmation",
                      sender="recruiter@example.com", body="Please reply to confirm the interview.", day=1)
    db.add(EmailAnalysis(email_id=email.id, category="JOB", priority="HIGH", priority_score=80,
                         requires_response=True, response_confidence=90))
    db.commit()
    result = answer_assistant_question(db, "Which conversations am I waiting to reply to?")
    assert result["intent"] == "unanswered"
    assert result["matched_count"] == 1
    assert result["evidence"][0]["thread_id"] == "wait-thread"
    assert result["write_actions_taken"] is False
