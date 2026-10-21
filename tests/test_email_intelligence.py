from types import SimpleNamespace

from backend.services.email_classifier import classify_email, detect_deadline


def msg(sender="recruiter@example.com", subject="Interview invitation", snippet="Please reply to confirm your interview.", labels=None):
    return SimpleNamespace(sender=sender, subject=subject, snippet=snippet, labels=labels or [])


def test_job_email_is_classified_and_action_detected():
    result = classify_email(msg())
    assert result["category"] == "JOB"
    assert result["priority"] in {"HIGH", "URGENT"}
    assert result["requires_response"] is True
    assert "REPLY" in result["detected_actions"]
    assert "ATTEND_INTERVIEW" in result["detected_actions"]


def test_promotional_label_is_respected():
    result = classify_email(msg(sender="store@example.com", subject="Weekly offers", snippet="Save 20 percent", labels=["CATEGORY_PROMOTIONS"]))
    assert result["category"] == "PROMOTIONS"


def test_unknown_email_falls_back_to_other():
    result = classify_email(msg(sender="person@example.com", subject="Hello there", snippet="Just saying hello", labels=[]))
    assert result["category"] == "OTHER"


def test_explicit_iso_deadline_is_parsed_utc():
    value = detect_deadline("Please submit by 2026-11-03")
    assert value is not None
    assert value.year == 2026 and value.month == 11 and value.day == 3
    assert value.utcoffset().total_seconds() == 0


def test_invalid_iso_deadline_is_ignored():
    assert detect_deadline("Deadline: 2026-99-99") is None


def test_internship_is_job_category_with_explanatory_reason():
    result = classify_email(msg(sender="careers@internshala.com", subject="Software internship opportunity", snippet="Submit your application by 2026-11-03"))
    assert result["category"] == "JOB"
    assert "internship-related" in result["classification_reason"]
    assert result["detected_deadline"] is not None
    assert "APPLY_FOR_JOB" in result["detected_actions"]


def test_urgent_account_security_signal_gets_urgent_priority():
    result = classify_email(msg(sender="security@example.com", subject="Account suspended", snippet="Immediate action required"))
    assert result["priority"] == "URGENT"
    assert result["priority_score"] >= 85


def test_routine_read_promotion_is_low_priority():
    result = classify_email(msg(sender="offers@example.com", subject="Weekly sale", snippet="Save up to 20 percent", labels=["CATEGORY_PROMOTIONS"]))
    assert result["category"] == "PROMOTIONS"
    assert result["priority"] == "LOW"


def test_named_month_deadline_is_parsed():
    value = detect_deadline("Please submit by November 3, 2026")
    assert value is not None
    assert (value.year, value.month, value.day) == (2026, 11, 3)


def test_weekday_deadline_means_next_occurrence():
    from datetime import datetime, timezone
    value = detect_deadline("Please send the documents by Friday", now=datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc))
    assert value is not None
    assert (value.year, value.month, value.day) == (2026, 10, 23)


def test_classification_includes_concise_summary():
    result = classify_email(msg(subject="Interview invitation", snippet="Please reply to confirm your interview."))
    assert result["summary"].startswith("Interview invitation:")
    assert result["requires_response"] is True


def test_relative_days_deadline_is_detected():
    from datetime import datetime, timezone
    value = detect_deadline("Please submit within 3 days", now=datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc))
    assert value is not None
    assert (value.year, value.month, value.day) == (2026, 10, 24)


def test_payment_overdue_is_urgent_and_actionable():
    result = classify_email(msg(sender="billing@example.com", subject="Payment overdue", snippet="Final notice: payment overdue. Pay by today."))
    assert result["category"] == "FINANCE"
    assert result["priority"] == "URGENT"
    assert "MAKE_PAYMENT" in result["detected_actions"]
    assert result["detected_deadline"] is not None


def test_follow_up_signal_is_detected():
    result = classify_email(msg(subject="Application update", snippet="Following up on your application. Any update on my application?"))
    assert "FOLLOW_UP" in result["detected_actions"]
