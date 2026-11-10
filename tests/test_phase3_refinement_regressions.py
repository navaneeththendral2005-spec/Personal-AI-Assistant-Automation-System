from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage, GmailThreadSyncState
from backend.models_email_intelligence import EmailAnalysis
from backend.services.email_intelligence_service import answer_inbox_question
from backend.services.email_response_tracking_service import list_unanswered_threads
from backend.services.email_classifier import classify_email
from backend.services.gmail_sync_service import sync_stored_thread_histories


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    return engine, session


def _add_email(db, *, gmail_id, thread_id, sender, subject, body, labels=None, day=10):
    row = EmailMessage(
        gmail_message_id=gmail_id,
        thread_id=thread_id,
        sender=sender,
        recipient="user@example.com",
        subject=subject,
        message_date=datetime(2026, 11, day, 10, tzinfo=timezone.utc),
        snippet=body[:160],
        body_text=body,
        labels=labels or ["INBOX"],
    )
    db.add(row)
    db.flush()
    return row


def test_specific_career_search_excludes_linkedin_digest_and_unrelated_date():
    engine, db = _db()
    try:
        invite = _add_email(
            db, gmail_id="real-invite", thread_id="thread-invite",
            sender="Recruiter <talent@company.example>",
            subject="Interview invitation - QA Engineer",
            body="We would like to invite you to a technical interview on November 12, 2026. Please confirm your availability.",
        )
        assessment = _add_email(
            db, gmail_id="real-assessment", thread_id="thread-assessment",
            sender="Talent Team <assessments@company.example>",
            subject="Technical assessment deadline",
            body="Please complete the coding assessment by November 15, 2026.",
        )
        recruiter = _add_email(
            db, gmail_id="real-recruiter", thread_id="thread-recruiter",
            sender="Recruiter <recruiter@company.example>",
            subject="Availability for next hiring round",
            body="Please reply with your availability for the next hiring round.",
        )
        linkedin = _add_email(
            db, gmail_id="linkedin-digest", thread_id="thread-linkedin",
            sender="LinkedIn <notifications-noreply@linkedin.com>",
            subject="Priyangaa posted: I am #HIRING | Test Manager",
            body=("Shared by your network. We are looking for a seasoned professional. "
                  "Start Date: Immediate. An assessment process may apply. "
                  "Applications close 16 November 2026. Explore hundreds of opportunities."),
        )
        generic = _add_email(
            db, gmail_id="generic-job", thread_id="thread-generic",
            sender="Jobs Digest <jobs@digest.example>",
            subject="Many exciting jobs this week",
            body="Explore hundreds of new job opportunities and career tips.",
        )
        prep = _add_email(
            db, gmail_id="prep-course", thread_id="thread-prep",
            sender="Naukri Campus <information@naukri.example>",
            subject="Interview preparation course",
            body="Prepare for interviews with our practice course.",
        )
        db.add_all([
            EmailAnalysis(email_id=invite.id, category="JOB", priority="HIGH", priority_score=85,
                          requires_response=True, response_confidence=90, career_signal=True),
            EmailAnalysis(email_id=assessment.id, category="JOB", priority="HIGH", priority_score=80,
                          detected_deadline=datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc), career_signal=True),
            EmailAnalysis(email_id=recruiter.id, category="JOB", priority="NORMAL", priority_score=60,
                          requires_response=True, response_confidence=90, career_signal=True),
            EmailAnalysis(email_id=linkedin.id, category="JOB", priority="HIGH", priority_score=78,
                          detected_deadline=datetime(2026, 11, 16, 23, 59, tzinfo=timezone.utc), career_signal=True,
                          requires_response=True, response_confidence=68),
            EmailAnalysis(email_id=generic.id, category="JOB", priority="NORMAL", priority_score=40,
                          career_signal=True, career_stage="OPPORTUNITY"),
            EmailAnalysis(email_id=prep.id, category="JOB", priority="NORMAL", priority_score=35,
                          career_signal=True, career_stage="OPPORTUNITY"),
        ])
        db.commit()

        result = answer_inbox_question(
            db,
            "Find actual interview invitations, assessment deadlines, and recruiter messages requesting a response. "
            "Exclude interview preparation courses and general job advertisements.",
        )
        evidence_ids = {item["email_id"] for item in result["evidence"]}
        assert evidence_ids == {invite.id, assessment.id, recruiter.id}
        assert linkedin.id not in evidence_ids
        assert generic.id not in evidence_ids
        assert prep.id not in evidence_ids
        assert result["provider"] == "local_evidence"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_thread_history_backfill_is_checkpointed_and_upserts_all_messages():
    engine, db = _db()
    try:
        _add_email(db, gmail_id="known-a", thread_id="thread-a", sender="a@example.com",
                   subject="Question", body="Please reply.")
        _add_email(db, gmail_id="known-b", thread_id="thread-b", sender="b@example.com",
                   subject="Update", body="FYI.")
        db.commit()

        fetched = []
        def fetch_thread(thread_id):
            fetched.append(thread_id)
            if thread_id == "thread-a":
                return [
                    {"id": "known-a", "thread_id": "thread-a", "sender": "a@example.com",
                     "subject": "Question", "body_text": "Please reply.", "labels": ["INBOX"]},
                    {"id": "sent-a", "thread_id": "thread-a", "sender": "user@example.com",
                     "subject": "Re: Question", "body_text": "I replied.", "labels": ["SENT"]},
                ]
            return [{"id": "known-b", "thread_id": "thread-b", "sender": "b@example.com",
                     "subject": "Update", "body_text": "FYI.", "labels": ["INBOX"]}]

        first = sync_stored_thread_histories(db, max_threads=1, thread_fetcher=fetch_thread)
        assert first["threads_examined"] == 1
        assert first["verified_local_thread_count"] == 1
        assert first["remaining_unverified_threads"] == 1

        second = sync_stored_thread_histories(db, max_threads=1, thread_fetcher=fetch_thread)
        assert second["threads_examined"] == 1
        assert second["verified_local_thread_count"] == 2
        assert second["remaining_unverified_threads"] == 0
        assert second["coverage_status"] == "STORED_THREADS_VERIFIED"
        assert second["mailbox_wide_guarantee"] is False
        assert fetched == ["thread-a", "thread-b"]
        assert db.query(EmailMessage).filter(EmailMessage.gmail_message_id == "sent-a").one_or_none() is not None
        assert db.query(GmailThreadSyncState).filter(GmailThreadSyncState.sync_status == "VERIFIED").count() == 2
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_unanswered_coverage_reflects_verified_local_threads_but_not_whole_mailbox():
    engine, db = _db()
    try:
        incoming = _add_email(db, gmail_id="pending-in", thread_id="pending-thread",
                              sender="Recruiter <recruiter@company.example>",
                              subject="Interview availability", body="Please reply with your availability.")
        db.add(EmailAnalysis(email_id=incoming.id, category="JOB", priority="HIGH", priority_score=80,
                             requires_response=True, response_confidence=90))
        db.add(GmailThreadSyncState(gmail_thread_id="pending-thread", sync_status="VERIFIED",
                                    message_count=1, last_synced_at=datetime.now(timezone.utc)))
        db.commit()
        result = list_unanswered_threads(db)
        assert result["total_unanswered_threads"] == 1
        assert result["coverage_status"] == "STORED_THREADS_VERIFIED"
        assert result["complete_thread_guarantee"] is True
        assert result["mailbox_wide_guarantee"] is False
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_hackquest_is_not_classified_as_employment_but_deadline_remains_actionable():
    from types import SimpleNamespace
    email = SimpleNamespace(
        sender="TCS <events@tcs.example>", recipient="user@example.com",
        subject="TCS HackQuest Season 11 | Showcase Your Skills | Closes 16 November 2026",
        snippet="The hunt for the next Best Ethical Hacker begins.",
        body_text="TCS HackQuest is open to eligible students. The competition closes 16 November 2026.",
        labels=["INBOX", "UNREAD"],
    )
    result = classify_email(email)
    assert result["category"] == "OTHER"
    assert result["career_signal"] is False
    assert result["detected_deadline"] is not None
    assert result["priority"] in {"HIGH", "URGENT"}
