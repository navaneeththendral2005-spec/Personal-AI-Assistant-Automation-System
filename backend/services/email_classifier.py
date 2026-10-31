from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

CATEGORIES = ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")
PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")

JOB_TERMS = (
    "job", "career", "recruit", "interview", "application", "hiring", "vacancy",
    "internship", "intern", "resume", "cv", "offer letter", "shortlisted",
    "assessment test", "coding challenge", "campus placement", "walk-in interview",
    "graduate trainee", "job alert", "placement drive", "career opportunity",
    "technical round", "hr round", "selection process", "joining letter",
)
FINANCE_TERMS = (
    "invoice", "receipt", "payment", "bank", "transaction", "statement", "bill due",
    "refund", "salary", "tax", "debit", "credit card", "emi", "account balance",
    "amount due", "payment failed", "subscription renewal", "minimum due",
)
PROMO_TERMS = (
    "unsubscribe", "discount", "sale", "promo", "limited time", "shop now",
    "free trial", "special price", "buy one get one", "coupon", "save up to",
    "exclusive offer", "flash sale", "deal of the day",
)
UPDATE_TERMS = (
    "newsletter", "weekly digest", "product update", "release notes", "webinar",
    "event update", "security alert", "verification code", "one-time password", "otp",
    "delivery update", "order shipped", "status update", "successfully received",
)
ACTION_TERMS = (
    "please reply", "please respond", "let me know", "confirm your", "submit",
    "complete by", "deadline", "schedule", "interview", "action required",
    "respond by", "requires your attention", "please review", "review and approve",
)
URGENT_TERMS = (
    "action required immediately", "account suspended", "payment overdue", "final notice",
    "respond immediately", "expires today", "immediate action required", "past due",
    "service will be suspended", "fraud alert", "security breach", "last chance to respond",
    "overdue payment", "account will be closed", "urgent action required",
)
HIGH_TERMS = (
    "interview invitation", "interview scheduled", "shortlisted", "offer letter",
    "application deadline", "deadline", "due today", "action required", "please respond",
    "meeting invitation", "payment due", "bill due", "invoice due", "respond by",
    "assessment scheduled", "complete your application", "document submission",
    "confirm your attendance", "interview details",
)
RESPONSE_TERMS = (
    "please reply", "please respond", "let me know", "confirm your", "respond by",
    "reply to this email", "could you reply", "please get back to me",
    "awaiting your response", "kindly respond", "please confirm", "confirm your availability",
    "let us know", "reply at your earliest", "respond at your earliest",
)

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _text(message: Any) -> str:
    values = [getattr(message, key, None) for key in ("sender", "subject", "snippet", "body_text")]
    values.extend(getattr(message, "labels", None) or [])
    return " ".join(str(value or "") for value in values).lower()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _find_urgent_match(text: str) -> str | None:
    """Find actionable urgent signals while filtering negation and educational context."""

    negation_pattern = re.compile(
        r"\b(?:no|not|never|without|isn't|aren't|wasn't|weren't|"
        r"hasn't|haven't|hadn't|doesn't|don't|didn't)\b"
        r"(?:\W+\w+){0,5}\W*$",
        re.IGNORECASE,
    )

    informational_pattern = re.compile(
        r"\b(?:how|learn|understand|explains?|explained|guide|"
        r"article|newsletter|tutorial|prevent|prevention)\b",
        re.IGNORECASE,
    )

    for term in URGENT_TERMS:
        pattern = re.compile(re.escape(term), re.IGNORECASE)

        for match in pattern.finditer(text):
            start = text.rfind(".", 0, match.start()) + 1
            sentence_end = text.find(".", match.end())

            if sentence_end == -1:
                sentence_end = len(text)

            sentence = text[start:sentence_end].strip()
            prefix = text[start:match.start()]

            # Ignore urgent phrases explicitly negated in their sentence.
            if negation_pattern.search(prefix):
                continue

            # Security-breach discussions are not necessarily active incidents.
            if term == "security breach":
                if informational_pattern.search(sentence):
                    continue

            return term

    return None

def _deadline_end_of_day(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime.combine(datetime(year, month, day).date(), time(23, 59), tzinfo=timezone.utc)
    except ValueError:
        return None


def detect_deadline(text: str, now: datetime | None = None) -> datetime | None:
    """Detect a plausible deadline in locally available email text.

    Dates are only extracted when the text contains deadline/action context. This
    deliberately avoids treating every date mentioned in a newsletter or email
    footer as a deadline. Date-only deadlines resolve to 23:59 UTC; that timezone
    is a storage convention, not a claim about the sender's timezone.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    lowered = re.sub(r"\s+", " ", str(text or "").lower())

    # A date is not automatically a deadline. Require a nearby semantic cue.
    deadline_context = re.search(
        r"\b(deadline|due|by|before|no later than|submit|submission|apply|application closes?|"
        r"closing date|last date|expires?|expiry|complete(?:d)? by|pay by|respond by|"
        r"register by|book by|confirm by|ends? on|must be received|within)\b",
        lowered,
    )
    relative_context = re.search(
        r"\b(today|tomorrow|this week|next week|in\s+\d{1,3}\s+(?:hours?|days?|weeks?)|"
        r"within\s+\d{1,3}\s+(?:hours?|days?|weeks?)|end of (?:this )?week)\b",
        lowered,
    )
    if not deadline_context and not relative_context:
        return None

    iso_match = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])\b", lowered)
    if iso_match:
        return _deadline_end_of_day(*(int(part) for part in iso_match.groups()))

    month_pattern = "|".join(sorted(MONTHS, key=len, reverse=True))
    match = re.search(rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b", lowered)
    if match:
        month, day, year = match.groups()
        return _deadline_end_of_day(int(year), MONTHS[month.rstrip(".")], int(day))
    match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})\.?[,]?\s+(20\d{{2}})\b", lowered)
    if match:
        day, month, year = match.groups()
        return _deadline_end_of_day(int(year), MONTHS[month.rstrip(".")], int(day))

    if re.search(r"\b(?:by\s+)?today\b|\bdue today\b|\bexpires today\b", lowered):
        return now.replace(hour=23, minute=59, second=0, microsecond=0)
    if re.search(r"\b(?:by\s+)?tomorrow\b|\bdue tomorrow\b|\btomorrow is the deadline\b", lowered):
        return (now + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)

    in_days = re.search(r"\b(?:in|within)\s+(\d{1,3})\s+days?\b", lowered)
    if in_days:
        count = int(in_days.group(1))
        if 0 <= count <= 365:
            return (now + timedelta(days=count)).replace(hour=23, minute=59, second=0, microsecond=0)
    in_weeks = re.search(r"\b(?:in|within)\s+(\d{1,2})\s+weeks?\b", lowered)
    if in_weeks:
        count = int(in_weeks.group(1))
        if 0 <= count <= 52:
            return (now + timedelta(days=count * 7)).replace(hour=23, minute=59, second=0, microsecond=0)
    in_hours = re.search(r"\b(?:in|within)\s+(\d{1,3})\s+hours?\b", lowered)
    if in_hours:
        count = int(in_hours.group(1))
        if 0 <= count <= 24 * 365:
            return now + timedelta(hours=count)

    if re.search(r"\b(?:by|before|due)\s+(?:the\s+)?end of (?:this )?week\b", lowered):
        days_ahead = 4 - now.weekday()
        if days_ahead < 0:
            days_ahead += 7
        target_date = (now + timedelta(days=days_ahead)).date()
        return _deadline_end_of_day(target_date.year, target_date.month, target_date.day)

    weekday_map = {name: idx for idx, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
    weekday_match = re.search(r"\b(?:by|before|due(?:\s+on)?|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    if weekday_match:
        target = weekday_map[weekday_match.group(1)]
        days_ahead = (target - now.weekday()) % 7 or 7
        if "next " in weekday_match.group(0) and days_ahead < 7:
            days_ahead += 7
        target_date = (now + timedelta(days=days_ahead)).date()
        return _deadline_end_of_day(target_date.year, target_date.month, target_date.day)

    # Month/day without a year is accepted only in deadline context.
    month_day = re.search(rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", lowered)
    if month_day:
        month, day = month_day.groups()
        month_num = MONTHS[month.rstrip(".")]
        candidate = _deadline_end_of_day(now.year, month_num, int(day))
        if candidate and candidate < now:
            candidate = _deadline_end_of_day(now.year + 1, month_num, int(day))
        return candidate
    month_day = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})\b", lowered)
    if month_day:
        day, month = month_day.groups()
        month_num = MONTHS[month.rstrip(".")]
        candidate = _deadline_end_of_day(now.year, month_num, int(day))
        if candidate and candidate < now:
            candidate = _deadline_end_of_day(now.year + 1, month_num, int(day))
        return candidate
    return None


def _make_summary(subject: str, snippet: str) -> str:
    clean_subject = " ".join(subject.split()) or "(no subject)"
    clean_snippet = " ".join(snippet.split())
    if not clean_snippet:
        return clean_subject[:240]
    return f"{clean_subject}: {clean_snippet}"[:500]


def _response_signal(text: str) -> tuple[bool, int, str]:
    """Detect explicit response requests while suppressing common no-reply notices."""
    negative_patterns = (
        r"\bno reply (?:is )?needed\b", r"\bno response (?:is )?required\b",
        r"\bdo not reply\b", r"\bdon't reply\b", r"\bno need to respond\b",
        r"\bthis is an automated message\b", r"\bplease do not respond\b",
    )
    if any(re.search(pattern, text) for pattern in negative_patterns):
        return False, 0, "A no-reply/automated-message signal was found."
    direct = (
        "please reply", "please respond", "confirm your availability", "confirm your attendance",
        "please confirm", "respond by", "reply to this email", "awaiting your response",
        "kindly respond", "please get back to me", "let me know", "let us know",
    )
    matched = next((term for term in direct if term in text), None)
    if matched:
        return True, 90, f"Explicit response request: '{matched}'."
    weaker = ("any update on my application", "following up on", "could you share", "please review")
    matched = next((term for term in weaker if term in text), None)
    if matched:
        return True, 68, f"Possible response/follow-up request: '{matched}'."
    return False, 0, "No clear response request detected in available metadata/snippet."


def _deadline_type(text: str, deadline: datetime | None) -> tuple[str, int]:
    if deadline is None:
        return "NONE", 0
    if any(term in text for term in ("internship application", "job application", "apply by", "application deadline", "submit your application", "resume submission")):
        kind = "APPLICATION"
    elif any(term in text for term in ("interview", "assessment", "technical round", "hr round", "screening call")):
        kind = "INTERVIEW"
    elif any(term in text for term in ("payment", "invoice", "bill due", "amount due", "minimum due", "tax payment")):
        kind = "PAYMENT"
    elif any(term in text for term in ("meeting", "webinar", "event", "appointment", "registration")):
        kind = "EVENT"
    else:
        kind = "GENERAL"
    deadline_words = ("deadline", "due", "by ", "before", "submit", "expires", "closing date", "last date", "within", "tomorrow", "today")
    explicit_context = any(term in text for term in deadline_words)
    # This is a heuristic confidence, not a calibrated probability.
    confidence = 92 if explicit_context else 55
    return kind, confidence


def _career_signals(text: str, sender: str) -> tuple[bool, str, int, list[str]]:
    """Extract conservative career-stage signals from locally stored email text."""
    stage_patterns = (
        ("INTERVIEW", ("interview invitation", "interview scheduled", "technical interview", "hr interview", "screening call", "assessment scheduled")),
        ("OUTCOME", ("offer letter", "pleased to offer", "job offer", "we regret to inform", "application rejected", "not selected", "you have been shortlisted", "shortlisted for")),
        ("FOLLOW_UP", ("following up on your application", "follow up on your application", "haven't heard back", "have not heard back", "any update on my application")),
        ("APPLICATION", ("application submitted", "application received", "thank you for applying", "apply now", "apply for this job", "submit your application", "application deadline", "complete your application", "submit your resume", "submit your cv")),
        ("OPPORTUNITY", ("job opportunity", "career opportunity", "new opportunity", "internship opportunity", "internship opening", "job alert", "job opening", "graduate trainee", "hiring", "vacancy", "internship", "intern role", "intern position", "placement drive")),
    )
    for stage, terms in stage_patterns:
        matched = [term for term in terms if term in text]
        if matched:
            confidence = 92 if stage in ("INTERVIEW", "OUTCOME", "FOLLOW_UP", "APPLICATION") else 82
            evidence = [f"{stage}:{term}" for term in matched[:4]]
            return True, stage, confidence, evidence
    # Sender alone is a weak lead signal; don't treat it as proof of a specific opportunity.
    career_sender = any(term in sender for term in ("naukri", "internshala", "linkedin", "indeed", "greenhouse", "lever", "workday", "recruit", "careers", "jobs"))
    if career_sender:
        return True, "POSSIBLE_OPPORTUNITY", 55, ["CAREER_SENDER_ONLY"]
    return False, "NONE", 0, []


def _action_evidence(text: str, actions: list[str]) -> list[dict[str, str]]:
    """Return short matched phrase evidence for detected actions, without email-body assumptions."""
    terms = {
        "REPLY": ("please reply", "please respond", "let me know", "respond by", "reply to this email", "awaiting your response", "kindly respond"),
        "APPLY_FOR_JOB": ("apply now", "apply for this job", "application deadline", "submit your application", "submit a resume", "submit your cv", "complete your application"),
        "ATTEND_INTERVIEW": ("interview invitation", "interview scheduled", "attend the interview", "join the interview", "technical round", "hr round", "assessment scheduled", "screening call", "recruiter call"),
        "MAKE_PAYMENT": ("payment due", "bill due", "invoice due", "pay by", "payment overdue", "amount due", "minimum due"),
        "CONFIRM": ("please confirm", "confirm your attendance", "confirm your booking", "confirm your availability", "confirm your interview"),
        "REVIEW_DOCUMENT": ("please review", "review and approve", "document for review", "approval required", "submit the documents"),
        "VERIFY_ACCOUNT": ("verify your account", "verification code", "one-time password", "otp", "security alert"),
        "TRACK_DELIVERY": ("track your order", "order shipped", "out for delivery", "delivery update"),
        "SCHEDULE_MEETING": ("schedule a meeting", "book an appointment", "schedule an interview", "choose a time slot", "select an interview slot"),
        "FOLLOW_UP": ("follow up", "follow-up", "following up", "checking in", "any update on my application", "haven't heard back", "have not heard back"),
    }
    evidence = []
    for action in actions:
        matched = next((term for term in terms.get(action, ()) if term in text), None)
        if matched:
            evidence.append({"action": action, "matched_phrase": matched})
    return evidence


def classify_email(message: Any) -> dict[str, Any]:
    """Return explainable category, priority, response, action, deadline and summary."""
    text = _text(message)
    sender = str(getattr(message, "sender", "") or "").lower()
    subject = str(getattr(message, "subject", "") or "")
    snippet = str(getattr(message, "snippet", "") or "")
    body_text = str(getattr(message, "body_text", "") or "")
    labels = {str(label).upper() for label in (getattr(message, "labels", None) or [])}

    # Weighted category signals avoid a single weak word dominating a message.
    category_scores = {name: 0 for name in CATEGORIES}
    for category, terms in (("JOB", JOB_TERMS), ("FINANCE", FINANCE_TERMS), ("PROMOTIONS", PROMO_TERMS), ("UPDATES", UPDATE_TERMS)):
        category_scores[category] += min(sum(1 for term in terms if term in text), 4) * 2
    if "CATEGORY_PROMOTIONS" in labels:
        category_scores["PROMOTIONS"] += 6
    if "CATEGORY_UPDATES" in labels:
        category_scores["UPDATES"] += 4
    if "CATEGORY_PERSONAL" in labels:
        category_scores["PERSONAL"] += 3
    if any(pattern in sender for pattern in ("naukri", "linkedin", "indeed", "greenhouse", "lever", "workday", "recruit", "internshala", "careers", "jobs")):
        category_scores["JOB"] += 5
    if any(pattern in sender for pattern in ("bank", "groww", "paypal", "stripe", "billing", "razorpay", "invoice")):
        category_scores["FINANCE"] += 5
    if "UNREAD" in labels and category_scores["PERSONAL"] == 0:
        # Unread is not itself a category; it only helps tie-breaking through other signals.
        pass
    if not any(category_scores.values()):
        category_scores["OTHER"] = 1
    category = max(CATEGORIES, key=lambda name: (category_scores[name], -CATEGORIES.index(name)))
    winning_score = category_scores[category]
    runner_up = max((score for name, score in category_scores.items() if name != category), default=0)
    category_margin = max(0, winning_score - runner_up)
    category_confidence = min(96, 45 + winning_score * 4 + category_margin * 2) if winning_score else 35
    category_reason = (
        f"Matched {category.lower()} signals in sender, subject, body, snippet, or Gmail labels (signal score {winning_score})."
        if winning_score else "No strong category signals; classified as OTHER."
    )

    urgent_match = _find_urgent_match(text)
    high_match = next((term for term in HIGH_TERMS if term in text), None)
    action_match = next((term for term in ACTION_TERMS if term in text), None)
    unread = "UNREAD" in labels
    job_interview = any(term in text for term in ("interview", "technical round", "hr round", "assessment scheduled", "screening call", "recruiter call"))
    payment_signal = any(term in text for term in ("payment due", "invoice due", "bill due", "payment overdue", "amount due"))
    requires_response, response_confidence, response_reason = _response_signal(text)
    if urgent_match:
        score, priority = 95, "URGENT"
        priority_reason = f"Urgent signal: '{urgent_match}'."
    elif high_match or job_interview or (payment_signal and unread):
        score, priority = 78, "HIGH"
        priority_reason = (
            f"High-priority signal: "
            f"'{high_match or 'time-sensitive interview/payment signal'}'."
        )
    elif requires_response:
        score, priority = 65, "NORMAL"
        priority_reason = (
            f"Explicit response required: {response_reason}"
        )
    elif action_match:
        score, priority = 55, "NORMAL"
        priority_reason = f"Possible action signal: '{action_match}'."
    elif category == "PROMOTIONS" and not unread:
        score, priority = 10, "LOW"
        priority_reason = "Promotional message without a strong action signal."
    elif category in ("UPDATES", "PROMOTIONS") and not unread:
        score, priority = 18, "LOW"
        priority_reason = "Routine update without a strong action signal."
    else:
        score, priority = 35, "NORMAL"
        priority_reason = "No strong urgency signal detected."
    actions: list[str] = []
    action_patterns = (
        ("REPLY", ("please reply", "please respond", "let me know", "respond by", "reply to this email", "awaiting your response", "kindly respond", "please get back to me", "let us know")),
        ("APPLY_FOR_JOB", ("apply now", "apply for this job", "application deadline", "submit your application", "submit a resume", "submit your resume", "submit your cv", "complete your application", "applications close", "last date to apply")),
        ("ATTEND_INTERVIEW", ("interview invitation", "interview scheduled", "attend the interview", "join the interview", "technical round", "hr round", "assessment scheduled", "screening call", "recruiter call")),
        ("MAKE_PAYMENT", ("payment due", "bill due", "invoice due", "pay by", "payment overdue", "amount due", "minimum due", "complete the payment")),
        ("CONFIRM", ("please confirm", "confirm your attendance", "confirm your booking", "confirm your availability", "confirm your interview", "kindly confirm", "confirm by")),
        ("REVIEW_DOCUMENT", ("please review", "review and approve", "document for review", "approval required", "submit the documents", "upload the required documents")),
        ("VERIFY_ACCOUNT", ("verify your account", "verification code", "one-time password", "otp", "security alert")),
        ("TRACK_DELIVERY", ("track your order", "order shipped", "out for delivery", "delivery update")),
        ("SCHEDULE_MEETING", ("schedule a meeting", "book an appointment", "schedule an interview", "choose a time slot", "select an interview slot")),
        ("FOLLOW_UP", ("follow up", "follow-up", "following up", "checking in", "any update on my application", "haven't heard back", "have not heard back")),
    )
    for action, terms in action_patterns:
        if _has_any(text, terms):
            actions.append(action)

    action_evidence = _action_evidence(text, actions)
    career_signal, career_stage, career_confidence, career_evidence = _career_signals(text, sender)
    deadline = detect_deadline(text)
    deadline_type, deadline_confidence = _deadline_type(text, deadline)
    confidence_score = min(98, round(category_confidence * 0.55 + (75 if high_match or urgent_match else 55) * 0.25 + (response_confidence if requires_response else 55) * 0.20))
    reasons = [category_reason, priority_reason, response_reason]
    if requires_response:
        reasons.append("Likely requires a reply or confirmation; verify the original email.")
    if deadline:
        reasons.append(f"Possible {deadline_type.lower()} deadline detected (heuristic confidence {deadline_confidence}/100); verify its meaning and timezone against the original email.")
    if category == "JOB" and _has_any(text, ("internship", "intern", "internshala")):
        reasons.append("Likely internship-related; retained under the JOB category for compatibility.")
    if category == "JOB" and _has_any(text, ("interview", "shortlisted", "offer letter")):
        reasons.append("Potentially time-sensitive hiring-stage message.")
    return {
        "category": category,
        "priority": priority,
        "priority_score": score,
        "confidence_score": confidence_score,
        "classification_reason": " ".join(reasons),
        "requires_response": requires_response,
        "response_confidence": response_confidence,
        "detected_deadline": deadline,
        "deadline_type": deadline_type,
        "deadline_confidence": deadline_confidence,
        "detected_actions": actions,
        "action_evidence": action_evidence,
        "career_signal": career_signal,
        "career_stage": career_stage,
        "career_confidence": career_confidence,
        "career_evidence": career_evidence,
        "summary": _make_summary(subject, body_text or snippet),
    }
