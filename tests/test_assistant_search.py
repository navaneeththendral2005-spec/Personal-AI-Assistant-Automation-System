"""Regression tests for intent-aware, evidence-grounded FRIDAY chat search."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage, Task
from backend.models_email_intelligence import EmailAnalysis
from backend.services.assistant_query_service import answer_from_stored_data


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        interview = EmailMessage(
            gmail_message_id="interview-1", thread_id="t1", sender="recruiter@example.com",
            recipient="user@example.com", subject="Interview invitation - Python Developer",
            message_date=datetime(2026, 10, 21, 9, tzinfo=timezone.utc),
            snippet="Please confirm your technical interview on Friday.", labels=["UNREAD"],
        )
        internship = EmailMessage(
            gmail_message_id="intern-1", thread_id="t2", sender="careers@internshala.com",
            recipient="user@example.com", subject="AI/ML internship opportunity",
            message_date=datetime(2026, 10, 20, 9, tzinfo=timezone.utc),
            snippet="Submit your application by 2026-10-25.", labels=["INBOX"],
        )
        unrelated = EmailMessage(
            gmail_message_id="promo-1", thread_id="t3", sender="store@example.com",
            recipient="user@example.com", subject="Weekly discount newsletter",
            message_date=datetime(2026, 10, 19, 9, tzinfo=timezone.utc),
            snippet="Save 20 percent today.", labels=["CATEGORY_PROMOTIONS"],
        )
        session.add_all([interview, internship, unrelated])
        session.flush()
        session.add_all([
            EmailAnalysis(email_id=interview.id, category="JOB", priority="URGENT", priority_score=95,
                          classification_reason="Interview invitation; confirm availability", requires_response=True,
                          detected_deadline=datetime(2026, 10, 23, 23, 59, tzinfo=timezone.utc),
                          detected_actions=["ATTEND_INTERVIEW", "CONFIRM"], summary="Interview invitation"),
            EmailAnalysis(email_id=internship.id, category="JOB", priority="HIGH", priority_score=78,
                          classification_reason="Internship opportunity with application date", requires_response=False,
                          detected_deadline=datetime(2026, 10, 25, 23, 59, tzinfo=timezone.utc),
                          detected_actions=["APPLY_FOR_JOB"], summary="AI/ML internship opportunity"),
            EmailAnalysis(email_id=unrelated.id, category="PROMOTIONS", priority="LOW", priority_score=10,
                          classification_reason="Promotional content", requires_response=False,
                          detected_deadline=None, detected_actions=[], summary="Weekly discount newsletter"),
        ])
        session.commit()
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_interview_question_returns_matching_evidence_not_generic_summary(db):
    result = answer_from_stored_data(db, "Which emails mention interviews?")
    assert result["intent"] == "interviews"
    assert result["matched_emails"] == 1
    assert result["evidence"][0]["subject"] == "Interview invitation - Python Developer"
    assert "Why it matched" in result["answer"]


def test_internship_question_returns_internship_records(db):
    result = answer_from_stored_data(db, "Which internships should I follow up on?")
    assert result["intent"] == "internships"
    assert result["matched_emails"] == 1
    assert result["evidence"][0]["opportunity_stage"] == "OPPORTUNITY"


def test_empty_search_explains_local_metadata_scope(db):
    result = answer_from_stored_data(db, "Which emails mention a visa appointment?")
    assert result["matched_emails"] == 0
    assert "does not mean your Gmail inbox has no such messages" in result["answer"]
    assert result["write_actions_taken"] is False


def test_follow_up_question_uses_recent_topic_and_priority_filter(db):
    result = answer_from_stored_data(
        db, "Which of those are urgent?",
        history=[{"role": "user", "content": "Which emails mention interviews?"}],
    )
    assert result["intent"] == "interviews"
    assert result["matched_emails"] == 1
    assert result["evidence"][0]["priority"] == "URGENT"


def test_deadline_question_returns_only_records_with_detected_deadline(db):
    result = answer_from_stored_data(db, "Which emails have upcoming deadlines?")
    assert result["intent"] == "deadlines"
    assert result["matched_emails"] == 2
    assert all(item["detected_deadline"] for item in result["evidence"])


def test_overdue_deadline_query_excludes_future_deadlines(db):
    result = answer_from_stored_data(db, "Which email deadlines are overdue?")
    assert result["intent"] == "deadlines"
    assert result["matched_emails"] == 0


def test_task_question_reads_tasks_and_does_not_write(db):
    db.add(Task(title="Submit internship application", is_completed=False, due_at=None, reminder_sent=False))
    db.commit()
    result = answer_from_stored_data(db, "What tasks are pending?")
    assert result["intent"] == "tasks"
    assert result["matched_tasks"] == 1
    assert result["evidence"][0]["title"] == "Submit internship application"
    assert result["write_actions_taken"] is False


def test_blank_question_rejected(db):
    with pytest.raises(ValueError):
        answer_from_stored_data(db, "   ")


def test_combined_job_and_internship_question_returns_both_kinds(db):
    result = answer_from_stored_data(db, "Which internships and job opportunities should I follow up on?")
    assert result["intent"] == "opportunities"
    assert result["matched_emails"] == 2
    assert {item["subject"] for item in result["evidence"]} == {
        "Interview invitation - Python Developer", "AI/ML internship opportunity"
    }
