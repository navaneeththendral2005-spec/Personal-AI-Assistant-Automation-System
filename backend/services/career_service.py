"""Conservative career-opportunity extraction and explicit status tracking."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from sqlalchemy import or_
from sqlalchemy.orm import Session
from backend.models import EmailMessage
from backend.models_email_intelligence import EmailAnalysis
from backend.models_career import CareerOpportunity

CAREER_TERMS = ("job", "career", "recruit", "interview", "application", "hiring", "vacancy", "internship", "intern", "resume", "cv", "offer letter", "shortlisted", "graduate trainee", "placement", "apply now", "apply for this job")
STATUSES = ("DISCOVERED", "INTERESTED", "APPLIED", "INTERVIEWING", "OFFER", "REJECTED", "ARCHIVED")


def _extract_company(sender: str | None, subject: str | None) -> str | None:
    sender = (sender or "").strip()
    display = re.match(r'^\s*["\']?([^<"\']+?)["\']?\s*<', sender)
    if display:
        candidate = display.group(1).strip()
        if candidate and "@" not in candidate and len(candidate) < 100:
            return candidate
    email = re.search(r"([\w.+-]+)@([\w.-]+)", sender)
    if email:
        domain = email.group(2).lower().removeprefix("www.")
        root = domain.split(".")[0]
        known = {"naukri": "Naukri", "internshala": "Internshala", "linkedin": "LinkedIn", "indeed": "Indeed", "unstop": "Unstop", "greenhouse": "Greenhouse", "lever": "Lever", "workday": "Workday"}
        return known.get(root, root.replace("-", " ").title())
    return None


def _extract_role(subject: str | None, snippet: str | None) -> str | None:
    subject = " ".join((subject or "").split())
    snippet = " ".join((snippet or "").split())
    patterns = [r"(?:hiring|opening|opportunity|position|role|vacancy)\s+(?:for|as)\s+([^.!|]{3,100})", r"(?:internship|intern|job)\s+(?:for|as|in)\s+([^.!|]{3,100})"]
    for pattern in patterns:
        for source in (subject, snippet[:500]):
            match = re.search(pattern, source, re.I)
            if match:
                return match.group(1).strip(" -:|,.!")[:300]
    if subject and any(x in subject.lower() for x in ("engineer", "developer", "analyst", "intern", "trainee", "designer", "manager", "scientist")):
        return subject[:300]
    return None


def _extract_url(text: str | None) -> str | None:
    match = re.search(r"https?://[^\s<>\]\[)]+", text or "", re.I)
    if not match:
        return None
    url = match.group(0).rstrip(".,;!")
    # Do not return javascript/data or other non-web schemes.
    return url if url.lower().startswith(("https://", "http://")) else None


def sync_opportunities(db: Session, limit: int = 200) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    rows = db.query(EmailMessage, EmailAnalysis).outerjoin(EmailAnalysis, EmailAnalysis.email_id == EmailMessage.id).order_by(EmailMessage.message_date.desc(), EmailMessage.id.desc()).limit(limit).all()
    created = updated = skipped = 0
    try:
        for email, analysis in rows:
            text = " ".join([email.sender or "", email.subject or "", email.snippet or "", " ".join(email.labels or []), analysis.category if analysis else ""]).lower()
            category_job = bool(analysis and analysis.category == "JOB")
            explicit_career_signal = bool(analysis and getattr(analysis, "career_signal", False))
            if not explicit_career_signal and not category_job and not any(term in text for term in CAREER_TERMS):
                skipped += 1
                continue
            opportunity = db.query(CareerOpportunity).filter(CareerOpportunity.email_id == email.id).one_or_none()
            is_internship = any(t in text for t in ("internship", "internshala", "summer intern", "intern role"))
            status_signal = "DISCOVERED"
            # Infer stage only from explicit wording; user can always change the tracked status.
            if any(t in text for t in ("interview invitation", "interview scheduled", "technical interview")):
                status_signal = "INTERVIEWING"
            elif any(t in text for t in ("offer letter", "pleased to offer", "job offer")):
                status_signal = "OFFER"
            elif any(t in text for t in ("application received", "application submitted", "thank you for applying")):
                status_signal = "APPLIED"
            if opportunity is None:
                opportunity = CareerOpportunity(email_id=email.id, status=status_signal)
                db.add(opportunity)
                created += 1
            else:
                updated += 1
                # Keep user-selected status intact; only refresh extracted metadata.
            opportunity.company = opportunity.company or _extract_company(email.sender, email.subject)
            opportunity.role_title = opportunity.role_title or _extract_role(email.subject, email.snippet)
            opportunity.opportunity_type = "INTERNSHIP" if is_internship else "JOB"
            opportunity.application_url = opportunity.application_url or _extract_url((email.subject or "") + " " + (email.snippet or ""))
            if opportunity.status == "DISCOVERED" and status_signal != "DISCOVERED":
                opportunity.status = status_signal
            opportunity.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"created": created, "refreshed": updated, "skipped_non_career_emails": skipped, "scanned": len(rows), "note": "Career records are inferred from stored metadata/snippets. Review extracted company, role, links, and status against the original email."}


def list_opportunities(db: Session, status: str | None = None, opportunity_type: str | None = None, limit: int = 100) -> list[dict]:
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    query = db.query(CareerOpportunity, EmailMessage).join(EmailMessage, CareerOpportunity.email_id == EmailMessage.id)
    if status:
        status = status.upper()
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        query = query.filter(CareerOpportunity.status == status)
    if opportunity_type:
        opportunity_type = opportunity_type.upper()
        if opportunity_type not in ("JOB", "INTERNSHIP"):
            raise ValueError("type must be JOB or INTERNSHIP")
        query = query.filter(CareerOpportunity.opportunity_type == opportunity_type)
    rows = query.order_by(CareerOpportunity.follow_up_at.asc().nullslast(), EmailMessage.message_date.desc(), CareerOpportunity.id.desc()).limit(limit).all()
    result = []
    for item, email in rows:
        analysis = db.query(EmailAnalysis).filter(EmailAnalysis.email_id == email.id).one_or_none()
        result.append({"id": item.id, "email_id": email.id, "company": item.company, "role_title": item.role_title, "type": item.opportunity_type, "status": item.status, "application_url": item.application_url, "follow_up_at": item.follow_up_at.isoformat() if item.follow_up_at else None, "notes": item.notes, "subject": email.subject, "sender": email.sender, "message_date": email.message_date.isoformat() if email.message_date else None, "snippet": email.snippet, "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                       "career_stage": analysis.career_stage if analysis else "UNANALYZED", "career_confidence": analysis.career_confidence if analysis else 0,
                       "career_evidence": analysis.career_evidence or [] if analysis else [], "detected_deadline": analysis.detected_deadline.isoformat() if analysis and analysis.detected_deadline else None,
                       "deadline_type": analysis.deadline_type if analysis else "NONE", "deadline_confidence": analysis.deadline_confidence if analysis else 0,
                       "detected_actions": analysis.detected_actions or [] if analysis else [], "action_evidence": analysis.action_evidence or [] if analysis else []})
    return result


def update_opportunity(db: Session, opportunity_id: int, payload: dict) -> dict | None:
    item = db.get(CareerOpportunity, opportunity_id)
    if item is None:
        return None
    if "status" in payload and payload["status"] is not None:
        value = payload["status"].upper()
        if value not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        item.status = value
    if "follow_up_at" in payload:
        dt = payload["follow_up_at"]
        if dt is not None and dt.tzinfo is None:
            raise ValueError("follow_up_at must include a timezone offset")
        item.follow_up_at = dt
    if "notes" in payload:
        item.notes = payload["notes"]
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    return next((x for x in list_opportunities(db, limit=200) if x["id"] == item.id), None)



def career_summary(db: Session) -> dict:
    """Return pipeline counts and follow-up health from explicitly tracked opportunities."""
    from collections import Counter
    from datetime import timedelta

    items = db.query(CareerOpportunity).all()
    now = datetime.now(timezone.utc)
    statuses = Counter(item.status for item in items)
    types = Counter(item.opportunity_type for item in items)
    overdue = []
    upcoming = []
    for item in items:
        due = item.follow_up_at
        if due is None:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        due = due.astimezone(timezone.utc)
        if due < now and item.status not in ("OFFER", "REJECTED", "ARCHIVED"):
            overdue.append(item.id)
        elif now <= due <= now + timedelta(days=7) and item.status not in ("OFFER", "REJECTED", "ARCHIVED"):
            upcoming.append(item.id)
    return {
        "total_opportunities": len(items),
        "by_status": dict(statuses),
        "by_type": dict(types),
        "overdue_follow_ups": len(overdue),
        "upcoming_follow_ups_next_7_days": len(upcoming),
        "open_pipeline": sum(1 for item in items if item.status not in ("OFFER", "REJECTED", "ARCHIVED")),
        "note": "Status is user-controlled unless a clear stage signal is found in stored email metadata. Follow-up dates are reminders, not evidence that a message was sent.",
    }


def list_follow_ups(db: Session, *, window_days: int = 7, include_overdue: bool = True) -> list[dict]:
    """List actionable follow-ups, including overdue ones, without sending messages."""
    from datetime import timedelta
    if not 0 <= window_days <= 90:
        raise ValueError("window_days must be between 0 and 90")
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=window_days)
    rows = db.query(CareerOpportunity, EmailMessage).join(EmailMessage, CareerOpportunity.email_id == EmailMessage.id).filter(
        CareerOpportunity.follow_up_at.is_not(None),
        CareerOpportunity.status.notin_(("OFFER", "REJECTED", "ARCHIVED")),
    ).all()
    result = []
    for item, email in rows:
        due = item.follow_up_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        due = due.astimezone(timezone.utc)
        if due < now:
            if not include_overdue:
                continue
            state = "OVERDUE"
        elif due <= cutoff:
            state = "UPCOMING"
        else:
            continue
        result.append({"id": item.id, "email_id": email.id, "company": item.company, "role_title": item.role_title,
                       "type": item.opportunity_type, "status": item.status, "follow_up_at": due.isoformat(),
                       "follow_up_state": state, "subject": email.subject, "sender": email.sender,
                       "note": "Review the original email and confirm the next action manually."})
    return sorted(result, key=lambda x: x["follow_up_at"])
