"""Thread-aware detection of conversations that may still need the user's reply.

The service is intentionally read-only and uses only messages currently stored by
FRIDAY. Gmail's SENT label is the authoritative outgoing-message signal. Results
are conservative: a thread is considered unanswered only when its latest stored
message is incoming and its analysis indicates a response is required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models import EmailMessage, GmailThreadSyncState
from backend.models_email_intelligence import EmailAnalysis
from backend.services.email_classifier import _automated_sender, classify_email


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _is_sent(email: EmailMessage) -> bool:
    return any(str(label).upper() == "SENT" for label in (email.labels or []))


def _is_non_actionable_inbound(email: EmailMessage, analysis: EmailAnalysis | None) -> bool:
    """Suppress system, no-reply, spam and marketing messages from reply tracking."""
    labels = {str(label).upper() for label in (email.labels or [])}
    if labels.intersection({"SPAM", "TRASH", "DRAFT", "CATEGORY_PROMOTIONS"}):
        return True
    if _automated_sender(email.sender or ""):
        return True
    category = str(getattr(analysis, "category", "") or "").upper()
    if category in {"PROMOTIONS", "UPDATES"}:
        return True
    subject = str(email.subject or "").lower()
    automated_subject_terms = (
        "new sign-in", "new login", "security alert", "security notification",
        "verification code", "one-time password", "password reset", "account activity",
        "order shipped", "delivery update", "weekly digest", "newsletter",
    )
    return any(term in subject for term in automated_subject_terms)


def _thread_key(email: EmailMessage) -> str:
    return str(email.thread_id or f"message:{email.gmail_message_id}")


def _analysis_for(email: EmailMessage, analysis: EmailAnalysis | None) -> dict[str, Any]:
    if analysis is not None:
        return {
            "requires_response": bool(analysis.requires_response),
            "response_confidence": int(analysis.response_confidence or 0),
            "priority": analysis.priority,
            "priority_score": int(analysis.priority_score or 0),
            "summary": analysis.summary,
            "detected_deadline": analysis.detected_deadline,
        }
    result = classify_email(email)
    return {
        "requires_response": bool(result.get("requires_response")),
        "response_confidence": int(result.get("response_confidence", 0) or 0),
        "priority": result.get("priority", "NORMAL"),
        "priority_score": int(result.get("priority_score", 0) or 0),
        "summary": result.get("summary"),
        "detected_deadline": result.get("detected_deadline"),
    }


def list_unanswered_threads(
    db: Session,
    *,
    limit: int = 50,
    min_response_confidence: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return stored conversations whose latest stored message needs a reply.

    The API reports the number of stored messages per thread and explicitly
    marks the result as based on the locally synchronized subset; it cannot
    guarantee that every Gmail message in a thread has been synchronized.
    """
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if not 0 <= min_response_confidence <= 100:
        raise ValueError("min_response_confidence must be between 0 and 100")

    rows = (
        db.query(EmailMessage, EmailAnalysis)
        .outerjoin(EmailAnalysis, EmailAnalysis.email_id == EmailMessage.id)
        .all()
    )
    grouped: dict[str, list[tuple[EmailMessage, EmailAnalysis | None]]] = {}
    for email, analysis in rows:
        grouped.setdefault(_thread_key(email), []).append((email, analysis))

    current = _aware(now) if now is not None else datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    for key, messages in grouped.items():
        messages.sort(
            key=lambda pair: (_aware(pair[0].message_date) or datetime.min.replace(tzinfo=timezone.utc), pair[0].id)
        )
        latest_email, latest_analysis = messages[-1]
        # A latest outgoing message means the user has already replied in the
        # currently stored conversation, regardless of the earlier inbound flag.
        if _is_sent(latest_email):
            continue

        if _is_non_actionable_inbound(latest_email, latest_analysis):
            continue

        signals = _analysis_for(latest_email, latest_analysis)
        if not signals["requires_response"]:
            continue
        confidence = signals["response_confidence"]
        if confidence < min_response_confidence:
            continue

        latest_date = _aware(latest_email.message_date)
        waiting_hours = max(0, int((current - latest_date).total_seconds() // 3600)) if latest_date else None
        results.append({
            "thread_id": latest_email.thread_id,
            "thread_key": key,
            "latest_email_id": latest_email.id,
            "gmail_message_id": latest_email.gmail_message_id,
            "subject": latest_email.subject or "(no subject)",
            "sender": latest_email.sender or "unknown sender",
            "recipient": latest_email.recipient,
            "latest_message_date": latest_date.isoformat() if latest_date else None,
            "stored_message_count": len(messages),
            "waiting_hours": waiting_hours,
            "requires_response": True,
            "response_confidence": confidence,
            "priority": signals["priority"],
            "priority_score": signals["priority_score"],
            "detected_deadline": signals["detected_deadline"].isoformat() if isinstance(signals["detected_deadline"], datetime) else None,
            "summary": signals["summary"],
            "latest_message_is_sent": False,
            "detection_basis": "latest stored message is not labeled SENT and its response analysis requires a reply",
        })

    results.sort(
        key=lambda item: (
            item["priority_score"],
            item["waiting_hours"] if item["waiting_hours"] is not None else -1,
        ),
        reverse=True,
    )
    selected = results[:limit]
    single_message_thread_count = sum(1 for messages in grouped.values() if len(messages) == 1)
    states = {state.gmail_thread_id: state for state in db.query(GmailThreadSyncState).all()}
    known_gmail_thread_ids = {str(messages[0][0].thread_id) for messages in grouped.values()
                              if messages[0][0].thread_id}
    verified_thread_ids = {thread_id for thread_id in known_gmail_thread_ids
                           if thread_id in states and states[thread_id].sync_status == "VERIFIED"}
    unverified_thread_count = max(0, len(grouped) - len(verified_thread_ids))
    complete_known_threads = bool(grouped) and unverified_thread_count == 0
    coverage_status = (
        "STORED_THREADS_VERIFIED" if complete_known_threads
        else "NO_LOCAL_THREADS" if not grouped
        else "PARTIAL_LOCAL_HISTORY"
    )
    return {
        "count": len(selected),
        "total_unanswered_threads": len(results),
        "limit": limit,
        "min_response_confidence": min_response_confidence,
        "threads": selected,
        "scope": "locally_stored_gmail_thread_ids_only",
        "coverage_status": coverage_status,
        "complete_thread_guarantee": complete_known_threads,
        "mailbox_wide_guarantee": False,
        "stored_thread_count": len(grouped),
        "single_message_thread_count": single_message_thread_count,
        "verified_stored_thread_count": len(verified_thread_ids),
        "unverified_stored_thread_count": unverified_thread_count,
        "note": (
            "Every locally known Gmail thread ID has been fetched successfully. This verifies those stored thread IDs only; "
            "it does not prove FRIDAY has discovered every thread in the Gmail mailbox."
            if complete_known_threads else
            "This is a conservative assessment of locally stored messages. Some locally known Gmail threads have not yet "
            "been fetched in full; run POST /gmail/intelligence/sync-and-analyze to continue the checkpointed history backfill. "
            "Verify candidate conversations in Gmail."
        ),
        "write_actions_taken": False,
    }
