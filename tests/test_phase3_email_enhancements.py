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


def test_unanswered_detection_ignores_automated_security_notifications(db):
    email = add_email(
        db, gmail_id="security-auto", thread="security-thread",
        subject="New sign-in to your account", sender="OpenAI <noreply@example.com>",
        body="We noticed a new sign-in. Please review your account security right away.", day=3,
    )
    db.add(EmailAnalysis(
        email_id=email.id, category="UPDATES", priority="HIGH", priority_score=78,
        requires_response=True, response_confidence=68,
        summary="Automated account security notification",
    ))
    db.commit()
    result = list_unanswered_threads(db)
    assert result["total_unanswered_threads"] == 0


def test_promotional_copy_does_not_become_high_priority_or_require_reply():
    from types import SimpleNamespace
    from backend.services.email_classifier import classify_email

    message = SimpleNamespace(
        sender="LeetCode <notifications@leetcode.com>",
        recipient="me@example.com",
        subject="LIMITED TIME OFFER — Last chance to respond",
        snippet="Save 30 percent. Limited time offer. Last chance to respond.",
        body_text="Save 30 percent on your annual subscription. Unsubscribe at any time.",
        labels=["CATEGORY_PROMOTIONS", "INBOX"],
    )
    result = classify_email(message)
    assert result["category"] == "PROMOTIONS"
    assert result["priority"] == "LOW"
    assert result["requires_response"] is False


def test_combined_interview_assessment_and_job_search_excludes_generic_promotions(db):
    job = add_email(
        db, gmail_id="job-alert", thread="job-alert-thread",
        subject="Software Engineer Job Opportunity", sender="careers@company.example",
        body="We are hiring a Software Engineer. Complete the technical assessment.", day=3,
    )
    promo = add_email(
        db, gmail_id="promo-interview", thread="promo-thread",
        subject="Interview preparation course discount", sender="offers@store.example",
        body="Buy our interview preparation course at a limited time discount.",
        labels=["CATEGORY_PROMOTIONS"], day=2,
    )
    db.add_all([
        EmailAnalysis(email_id=job.id, category="JOB", priority="HIGH", priority_score=78,
                      career_signal=True, career_stage="OPPORTUNITY", career_confidence=82),
        EmailAnalysis(email_id=promo.id, category="PROMOTIONS", priority="HIGH", priority_score=78,
                      career_signal=False, career_stage="NONE", career_confidence=0),
    ])
    db.commit()
    result = answer_assistant_question(
        db, "Find emails related to interviews, assessments, or job opportunities"
    )
    assert result["intent"] == "career"
    assert job.id in result["evidence_ids"]
    assert promo.id not in result["evidence_ids"]


def test_local_summary_omits_long_tracking_urls_and_stays_compact(db):
    email = add_email(
        db, gmail_id="summary-url", thread="summary-url-thread",
        subject="AI Internship Opportunity", sender="careers@example.com",
        body=("We are hiring interns. Apply by November 15, 2026. "
              "https://example.com/apply?" + "tracking=" + "x" * 300), day=3,
    )
    db.commit()
    result = summarize_inbox(db, use_ai=False)
    assert "application link: https://example.com/apply" in result["summary"]
    assert "tracking=" not in result["summary"]
    assert len(result["summary"]) < 3500


def test_inbox_ask_filters_interview_course_promotions_from_career_results(db, monkeypatch):
    from backend.services import email_intelligence_service as service

    monkeypatch.setattr(service, "_ai_summary", lambda prompt: None)
    job = add_email(
        db, gmail_id="ask-job", thread="ask-job-thread",
        subject="Technical assessment for Software Engineer role",
        sender="recruiter@company.example",
        body="Your technical assessment is scheduled. Please complete the coding test.", day=3,
    )
    promo = add_email(
        db, gmail_id="ask-promo", thread="ask-promo-thread",
        subject="Interview preparation course discount", sender="offers@store.example",
        body="Buy our interview preparation course at a limited time discount.",
        labels=["CATEGORY_PROMOTIONS"], day=2,
    )
    db.add_all([
        EmailAnalysis(email_id=job.id, category="JOB", priority="HIGH", priority_score=78,
                      career_signal=True, career_stage="INTERVIEW", career_confidence=92),
        EmailAnalysis(email_id=promo.id, category="PROMOTIONS", priority="LOW", priority_score=10,
                      career_signal=False, career_stage="NONE", career_confidence=0),
    ])
    db.commit()
    result = service.answer_inbox_question(
        db, "Find emails related to interviews, assessments, or job opportunities"
    )
    assert result["matched_emails"] == 1
    assert result["evidence"][0]["email_id"] == job.id


def test_specific_career_search_requires_real_interview_or_assessment_or_recruiter_action(db):
    invitation = add_email(
        db, gmail_id="specific-invite", thread="specific-invite-thread",
        subject="Interview invitation - Software Engineer", sender="recruiter@company.example",
        body="We would like to invite you to a technical interview on November 12. Please confirm your availability.", day=4,
    )
    prep = add_email(
        db, gmail_id="specific-prep", thread="specific-prep-thread",
        subject="Get ready for interviews", sender="info@naukri.example",
        body="Practice English and improve your confidence before placement interviews.", day=3,
    )
    assessment = add_email(
        db, gmail_id="specific-assess", thread="specific-assess-thread",
        subject="Technical assessment deadline", sender="talent@company.example",
        body="Please complete the coding assessment by November 15, 2026.", day=2,
    )
    recruiter_request = add_email(
        db, gmail_id="specific-recruiter-request", thread="specific-recruiter-request-thread",
        subject="Availability for next hiring round", sender="talent@company.example",
        body="Please reply with your availability for the next hiring round.", day=2,
    )
    generic_job = add_email(
        db, gmail_id="specific-job", thread="specific-job-thread",
        subject="Many exciting jobs this week", sender="jobs@digest.example",
        body="Explore hundreds of new job opportunities and career tips.", day=1,
    )
    db.add_all([
        EmailAnalysis(email_id=invitation.id, category="JOB", priority="HIGH", priority_score=85,
                      requires_response=True, response_confidence=90, career_signal=True,
                      career_stage="INTERVIEW", career_confidence=92),
        EmailAnalysis(email_id=prep.id, category="JOB", priority="NORMAL", priority_score=35,
                      requires_response=False, career_signal=True, career_stage="OPPORTUNITY", career_confidence=82),
        EmailAnalysis(email_id=assessment.id, category="JOB", priority="HIGH", priority_score=80,
                      requires_response=False, detected_deadline=datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc),
                      career_signal=True, career_stage="INTERVIEW", career_confidence=90),
        EmailAnalysis(email_id=recruiter_request.id, category="JOB", priority="NORMAL", priority_score=60,
                      requires_response=True, response_confidence=90, career_signal=True,
                      career_stage="FOLLOW_UP", career_confidence=90),
        EmailAnalysis(email_id=generic_job.id, category="JOB", priority="NORMAL", priority_score=35,
                      requires_response=False, career_signal=True, career_stage="OPPORTUNITY", career_confidence=82),
    ])
    db.commit()

    result = answer_assistant_question(
        db,
        "Find emails containing interview invitations, assessment deadlines, and recruiter requests that need my attention",
    )
    ids = result["evidence_ids"]
    assert invitation.id in ids
    assert assessment.id in ids
    assert recruiter_request.id in ids
    assert prep.id not in ids
    assert generic_job.id not in ids


def test_interview_preparation_copy_does_not_raise_job_priority():
    from types import SimpleNamespace
    from backend.services.email_classifier import classify_email

    message = SimpleNamespace(
        sender="Naukri Campus Jobs <information@naukri.example>",
        recipient="me@example.com",
        subject="Become interview ready with practice",
        snippet="Improve your confidence before placement interviews.",
        body_text="Take a free AI English test before your placement interviews. Practice to improve.",
        labels=["INBOX", "UNREAD"],
    )
    result = classify_email(message)
    assert result["priority"] != "HIGH"


def test_unanswered_response_exposes_partial_thread_coverage(db):
    add_email(
        db, gmail_id="coverage-one", thread="coverage-one-thread",
        subject="Project update", sender="person@example.com", body="Here is the update.", day=4,
    )
    db.commit()
    result = list_unanswered_threads(db)
    assert result["coverage_status"] == "PARTIAL_LOCAL_HISTORY"
    assert result["complete_thread_guarantee"] is False
    assert result["single_message_thread_count"] == 1



def test_gmail_intelligence_ask_applies_each_requested_career_facet(db, monkeypatch):
    from backend.services import email_intelligence_service as service

    monkeypatch.setattr(service, "_ai_summary", lambda prompt: None)
    invite = add_email(
        db, gmail_id="direct-invite", thread="direct-invite-thread",
        subject="Interview invitation - Backend Engineer", sender="recruiter@company.example",
        body="We invite you to a technical interview on November 12. Please confirm your availability.",
        day=5,
    )
    assessment = add_email(
        db, gmail_id="direct-assessment", thread="direct-assessment-thread",
        subject="Technical assessment deadline", sender="talent@company.example",
        body="Complete the coding assessment by November 15, 2026.", day=4,
    )
    recruiter = add_email(
        db, gmail_id="direct-recruiter", thread="direct-recruiter-thread",
        subject="Please confirm availability for next round", sender="talent@company.example",
        body="Please reply with your availability for the next hiring round.", day=3,
    )
    prep = add_email(
        db, gmail_id="direct-prep", thread="direct-prep-thread",
        subject="Interview preparation course", sender="offers@training.example",
        body="Prepare for interviews with our mock interview course.", labels=["CATEGORY_PROMOTIONS"], day=2,
    )
    generic = add_email(
        db, gmail_id="direct-job-digest", thread="direct-job-digest-thread",
        subject="Hundreds of exciting job opportunities", sender="jobs@digest.example",
        body="Explore general job advertisements and career tips.", day=1,
    )
    db.add_all([
        EmailAnalysis(email_id=invite.id, category="JOB", priority="HIGH", priority_score=85,
                      requires_response=True, response_confidence=90),
        EmailAnalysis(email_id=assessment.id, category="JOB", priority="HIGH", priority_score=80,
                      detected_deadline=datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc)),
        EmailAnalysis(email_id=recruiter.id, category="JOB", priority="NORMAL", priority_score=60,
                      requires_response=True, response_confidence=90),
        EmailAnalysis(email_id=prep.id, category="PROMOTIONS", priority="LOW", priority_score=10),
        EmailAnalysis(email_id=generic.id, category="JOB", priority="NORMAL", priority_score=35),
    ])
    db.commit()

    result = service.answer_inbox_question(
        db,
        "Find actual interview invitations, assessment deadlines, and recruiter messages requesting a response. Exclude interview preparation courses and general job advertisements.",
        limit=10,
    )
    evidence_ids = {item["email_id"] for item in result["evidence"]}
    assert evidence_ids == {invite.id, assessment.id, recruiter.id}
    assert result["matched_emails"] == 3


def test_competition_announcement_is_not_misclassified_as_job():
    from types import SimpleNamespace
    from backend.services.email_classifier import classify_email

    message = SimpleNamespace(
        sender="Gokul Dinakaran <gokul@example.com>",
        subject="TCS HackQuest Season 11 | Showcase Your Skills | Closes 16 November 2026",
        snippet="The hunt for the next Best Ethical Hacker begins.",
        body_text="TCS HackQuest is open to eligible students. This competition closes on 16 November 2026.",
        labels=["INBOX", "UNREAD"],
    )
    result = classify_email(message)
    assert result["category"] == "OTHER"
    assert result["detected_deadline"] is not None
