from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from backend.database import Base
from backend.models import EmailMessage, Task
from backend.models_email_intelligence import EmailAnalysis
from backend.models_career import CareerOpportunity  # ensure additive table is registered
from backend.services.assistant_query_service import answer_assistant_question
from backend.services.career_service import sync_opportunities, list_opportunities, update_opportunity
from backend.services.email_classifier import detect_deadline


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def add_email(db, message_id, subject, snippet, sender="recruiter@example.com", category="JOB", priority="NORMAL", score=35, deadline=None, actions=None, response=False):
    email = EmailMessage(gmail_message_id=message_id, thread_id=message_id, sender=sender, recipient="me@example.com", subject=subject, message_date=datetime.now(timezone.utc), snippet=snippet, labels=["INBOX"], synced_at=datetime.now(timezone.utc))
    db.add(email); db.flush()
    analysis = EmailAnalysis(email_id=email.id, category=category, priority=priority, priority_score=score, classification_reason="test evidence", requires_response=response, detected_deadline=deadline, detected_actions=actions or [], summary=subject, analysis_status="complete", analyzed_at=datetime.now(timezone.utc))
    db.add(analysis); db.commit(); db.refresh(email)
    return email


def test_interview_question_matches_only_interview_evidence(db_session):
    add_email(db_session, "a", "Interview invitation", "Your technical interview is scheduled for Friday.")
    add_email(db_session, "b", "Weekly job digest", "Browse new jobs and internships.")
    result = answer_assistant_question(db_session, "Which emails mention interviews?")
    assert result["intent"] == "interviews"
    assert result["matched_count"] == 1
    assert result["evidence"][0]["subject"] == "Interview invitation"
    assert "interview-related wording" in result["evidence"][0]["why_matched"]


def test_empty_interview_result_explains_metadata_scope(db_session):
    add_email(db_session, "a", "Weekly job digest", "Browse new jobs and internships.")
    result = answer_assistant_question(db_session, "Which emails mention interviews?")
    assert result["matched_count"] == 0
    assert "does not prove Gmail contains no such message" in result["answer"]


def test_followup_urgent_filters_prior_evidence_and_keeps_topic(db_session):
    low = add_email(db_session, "a", "Internship digest", "New internship listings.", category="JOB", priority="NORMAL", score=35)
    high = add_email(db_session, "b", "Internship interview invitation", "Interview invitation: please confirm.", category="JOB", priority="HIGH", score=78)
    first = answer_assistant_question(db_session, "Which internships should I review?")
    result = answer_assistant_question(db_session, "Which of those are urgent?", history=[
        {"role":"user", "content":"Which internships should I review?"},
        {"role":"assistant", "content":first["answer"], "evidence_ids":first["evidence_ids"]},
    ])
    assert result["is_follow_up"] is True
    assert result["matched_count"] == 1
    assert result["evidence"][0]["email_id"] == high.id


def test_deadline_intent_uses_normalized_analysis_deadline(db_session):
    deadline = datetime.now(timezone.utc) + timedelta(days=2)
    add_email(db_session, "a", "Application deadline", "Submit the resume by the closing date.", deadline=deadline, actions=["APPLY_FOR_JOB"])
    result = answer_assistant_question(db_session, "Which emails have upcoming deadlines?")
    assert result["intent"] == "deadlines"
    assert result["matched_count"] == 1
    assert result["evidence"][0]["detected_deadline"] is not None


def test_overdue_task_query_uses_due_time(db_session):
    task = Task(title="Submit application", description="", due_at=datetime.now(timezone.utc)-timedelta(days=1), is_completed=False, reminder_sent=False)
    db_session.add(task); db_session.commit()
    result = answer_assistant_question(db_session, "What tasks are overdue?")
    assert result["intent"] == "overdue_tasks"
    assert result["matched_count"] == 1
    assert result["evidence"][0]["title"] == "Submit application"


def test_career_sync_creates_opportunity_and_status_is_user_editable(db_session):
    email = add_email(db_session, "career-1", "AI/ML Internship opportunity", "Apply now: submit your resume by 2026-11-03", sender="Internshala <student@internshala.com>", category="JOB", actions=["APPLY_FOR_JOB"])
    result = sync_opportunities(db_session)
    assert result["created"] == 1
    rows = list_opportunities(db_session)
    assert len(rows) == 1
    assert rows[0]["type"] == "INTERNSHIP"
    updated = update_opportunity(db_session, rows[0]["id"], {"status":"INTERESTED", "notes":"Review eligibility"})
    assert updated["status"] == "INTERESTED"
    again = sync_opportunities(db_session)
    assert again["created"] == 0
    assert list_opportunities(db_session)[0]["status"] == "INTERESTED"


def test_deadline_detector_supports_weeks_and_month_day_without_year():
    now = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    weeks = detect_deadline("Please submit within 2 weeks", now=now)
    assert weeks is not None and weeks.date() == (now + timedelta(days=14)).date()
    month_day = detect_deadline("Application closes by October 25", now=now)
    assert month_day is not None and (month_day.year, month_day.month, month_day.day) == (2026, 10, 25)


def test_combined_job_and_internship_question_returns_both_types(db_session):
    add_email(db_session, "job-a", "Graduate Trainee opening", "Apply for the software graduate trainee role.", sender="careers@company.example", category="JOB")
    add_email(db_session, "intern-a", "AI internship opportunity", "Apply for an AI internship.", sender="student@internshala.com", category="JOB")
    result = answer_assistant_question(db_session, "Which internships and job opportunities should I follow up on?")
    assert result["intent"] == "career"
    assert result["matched_count"] == 2


def test_deadline_question_can_be_scoped_to_internships(db_session):
    deadline = datetime.now(timezone.utc) + timedelta(days=3)
    add_email(db_session, "intern-deadline", "Internship application deadline", "Submit your internship application by 2026-11-03.", category="JOB", deadline=deadline)
    add_email(db_session, "job-deadline", "Graduate trainee application deadline", "Submit your job application by 2026-11-03.", category="JOB", deadline=deadline)
    result = answer_assistant_question(db_session, "Which internships have deadlines?")
    assert result["intent"] == "deadlines"
    assert result["matched_count"] == 1
    assert result["evidence"][0]["email_id"]
    assert "internship" in result["evidence"][0]["why_matched"].lower() or "deadline" in result["evidence"][0]["why_matched"].lower()
