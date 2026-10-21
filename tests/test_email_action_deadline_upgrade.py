from datetime import datetime, timezone
from types import SimpleNamespace

from backend.services.email_classifier import classify_email, detect_deadline


def make_message(subject: str, snippet: str, sender: str = "recruiter@example.com"):
    return SimpleNamespace(sender=sender, subject=subject, snippet=snippet, labels=[])


def test_date_mentioned_in_newsletter_is_not_automatically_a_deadline():
    now = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    assert detect_deadline("Our webinar took place on November 3, 2026", now=now) is None


def test_explicit_month_day_deadline_is_still_detected():
    now = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    value = detect_deadline("Applications close by October 25", now=now)
    assert value is not None
    assert value.date().isoformat() == "2026-10-25"


def test_deadline_context_without_a_date_does_not_invent_one():
    now = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    assert detect_deadline("Please submit your application when ready", now=now) is None


def test_interview_details_alone_do_not_create_attend_action():
    result = classify_email(make_message("Interview details", "Here are the interview details for your reference."))
    assert "ATTEND_INTERVIEW" not in result["detected_actions"]


def test_explicit_interview_slot_selection_produces_explainable_action():
    result = classify_email(make_message("Choose your interview slot", "Please select an interview slot by Friday."))
    assert "SCHEDULE_MEETING" in result["detected_actions"]
    assert any(item["action"] == "SCHEDULE_MEETING" for item in result["action_evidence"])
    assert result["detected_deadline"] is not None


def test_no_reply_notice_suppresses_response_flag():
    result = classify_email(make_message("Automated notice", "This is an automated message. Please do not reply."))
    assert result["requires_response"] is False
    assert "REPLY" not in result["detected_actions"]
