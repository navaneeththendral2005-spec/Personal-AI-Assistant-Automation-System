"""Evidence-grounded, intent-aware queries over FRIDAY's locally stored data.

Queries only inspect FRIDAY's local database. They never contact Gmail live or
mutate records. Email content is untrusted evidence, not executable instructions.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models import EmailMessage, Task
from backend.models_email_intelligence import EmailAnalysis
from backend.services.email_classifier import classify_email

STOPWORDS = {
    "which", "what", "when", "where", "show", "tell", "me", "about", "the", "and", "for", "from", "that", "this", "are", "is", "was", "were", "have", "has", "had", "with", "into", "your", "my", "you", "should", "could", "would", "please", "emails", "email", "messages", "ones", "those", "them", "there", "their", "does", "do", "did", "can", "i", "we", "to", "of", "on", "in", "a", "an", "be", "it", "its", "recent", "stored", "find", "search", "list", "all", "any", "up", "next", "are",
}
INTENT_TERMS = {
    "interviews": ("interview", "technical round", "hr round", "screening call", "interview scheduled", "interview invitation", "assessment scheduled", "recruiter call"),
    "internships": ("internship", "internshala", "summer intern", "intern role", "intern position", "intern"),
    "jobs": ("job", "career", "hiring", "recruiter", "vacancy", "graduate trainee", "job opportunity", "job alert", "offer letter", "shortlisted", "application"),
    "deadlines": ("deadline", "due date", "due by", "before", "by tomorrow", "within", "expires", "closing date", "last date", "application closes", "submit by"),
    "urgent": ("urgent", "urgently", "high priority", "important", "critical", "needs attention"),
    "response": ("reply", "respond", "response", "confirm", "answer", "follow up", "follow-up"),
    "finance": ("payment", "invoice", "bill", "bank", "transaction", "refund", "salary", "amount due", "tax"),
    "promotions": ("promotion", "discount", "sale", "coupon", "unsubscribe", "offer"),
}


def _normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _to_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _email_text(email: EmailMessage, analysis: EmailAnalysis | None) -> str:
    parts = [email.sender, email.recipient, email.subject, email.snippet, " ".join(email.labels or [])]
    if analysis:
        parts.extend([analysis.category, analysis.priority, analysis.classification_reason,
                      " ".join(analysis.detected_actions or []), analysis.summary,
                      analysis.career_stage, " ".join(analysis.career_evidence or [])])
    else:
        fallback = classify_email(email)
        parts.extend([fallback.get("category"), fallback.get("priority"), fallback.get("classification_reason"),
                      " ".join(fallback.get("detected_actions", []))])
    return _normal(" ".join(str(x or "") for x in parts))


def _detect_intent(question: str) -> str:
    q = _normal(question)
    if any(x in q for x in ("overdue task", "overdue tasks", "tasks overdue", "past due task", "what tasks are overdue", "task is overdue")):
        return "overdue_tasks"
    if any(x in q for x in ("task", "todo", "to-do", "reminder", "what should i do", "my schedule")):
        return "tasks"
    if any(x in q for x in ("overdue deadline", "overdue deadlines", "deadlines overdue", "deadline is overdue", "deadlines are overdue", "past due deadline")):
        return "deadlines"
    if any(x in q for x in ("deadline", "deadlines", "due date", "due dates", "expires", "closing date", "last date")):
        return "deadlines"
    if any(x in q for x in ("interview", "interviews", "screening call", "technical round", "hr round")):
        return "interviews"
    has_internship = any(x in q for x in ("internship", "internships", "intern role", "intern position"))
    has_job = any(x in q for x in ("job", "jobs", "job opportunity", "job opportunities", "career opportunity", "career opportunities"))
    if has_internship and has_job:
        return "career"
    if has_internship:
        return "internships"
    if any(x in q for x in ("urgent", "urgently", "high priority", "important emails", "critical emails")):
        return "urgent"
    if any(x in q for x in ("need a response", "needs a reply", "need to reply", "should i reply", "respond to", "awaiting my response")):
        return "response"
    if any(x in q for x in ("follow up", "follow-up", "followup", "apply", "application", "job opportunity", "job opportunities", "job alert", "career opportunity", "careers", "recruiter", "shortlisted")):
        return "jobs"
    if any(x in q for x in ("payment", "invoice", "bill", "finance", "transaction", "refund", "salary")):
        return "finance"
    if any(x in q for x in ("promotion", "promotions", "discount", "sale emails")):
        return "promotions"
    if any(x in q for x in ("summarize", "summary", "inbox overview", "what's in my inbox", "what is in my inbox", "recent emails")):
        return "recent"
    return "search"


def _topic_from_history(history: list[dict[str, Any]] | None) -> str | None:
    for item in reversed(history or []):
        if item.get("role") == "user" and item.get("content"):
            intent = _detect_intent(str(item["content"]))
            if intent not in ("search", "urgent", "response"):
                return intent
    return None


def _payload(email: EmailMessage, analysis: EmailAnalysis | None) -> dict[str, Any]:
    fallback = classify_email(email) if analysis is None else None
    category = analysis.category if analysis else fallback["category"]
    priority = analysis.priority if analysis else fallback["priority"]
    score = analysis.priority_score if analysis else fallback["priority_score"]
    deadline = analysis.detected_deadline if analysis else fallback["detected_deadline"]
    actions = analysis.detected_actions if analysis else fallback["detected_actions"]
    reason = analysis.classification_reason if analysis else fallback["classification_reason"]
    return {
        "email_id": email.id, "gmail_message_id": email.gmail_message_id,
        "subject": email.subject or "(no subject)", "sender": email.sender or "unknown sender",
        "recipient": email.recipient, "message_date": email.message_date.isoformat() if email.message_date else None,
        "snippet": email.snippet or "", "labels": email.labels or [], "category": category,
        "priority": priority, "priority_score": score,
        "confidence_score": analysis.confidence_score if analysis else fallback.get("confidence_score", 50),
        "response_confidence": analysis.response_confidence if analysis else fallback.get("response_confidence", 0),
        "detected_deadline": deadline.isoformat() if deadline else None,
        "deadline_type": analysis.deadline_type if analysis else fallback.get("deadline_type", "NONE"),
        "deadline_confidence": analysis.deadline_confidence if analysis else fallback.get("deadline_confidence", 0),
        "detected_actions": actions or [],
        "action_evidence": (analysis.action_evidence or []) if analysis else fallback.get("action_evidence", []),
        "career_signal": analysis.career_signal if analysis else fallback.get("career_signal", False),
        "career_stage": analysis.career_stage if analysis else fallback.get("career_stage", "NONE"),
        "career_confidence": analysis.career_confidence if analysis else fallback.get("career_confidence", 0),
        "career_evidence": (analysis.career_evidence or []) if analysis else fallback.get("career_evidence", []),
        "requires_response": analysis.requires_response if analysis else bool(fallback["requires_response"]),
        "classification_reason": reason,
    }


def answer_assistant_question(
    db: Session,
    question: str,
    history: list[dict[str, Any]] | None = None,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Answer a question from locally stored email metadata, analysis, and tasks."""
    question = (question or "").strip()
    if not question:
        raise ValueError("Question cannot be blank.")
    if len(question) > 1000:
        raise ValueError("Question must be 1000 characters or fewer.")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50.")

    # Injectable clock makes deadline/task queries deterministic in tests.
    # Normalize naive datetimes as UTC and aware datetimes to UTC.
    now_utc = _to_aware(now) if now is not None else datetime.now(timezone.utc)

    history = history or []
    q = _normal(question)
    intent = _detect_intent(question)
    is_followup = bool(re.search(r"\b(those|them|these|that|they|which of|the first|the second|the last|same ones)\b", q))
    filter_priority = intent == "urgent" or (is_followup and any(w in q for w in ("urgent", "high priority", "important")))
    if is_followup and intent in ("search", "urgent", "response"):
        intent = _topic_from_history(history) or intent
    # Follow-up priority filters retain the previous topic, e.g. interviews -> urgent interviews.
    effective_intent = intent
    prior_ids: list[int] = []
    if is_followup:
        for item in reversed(history):
            if item.get("role") == "assistant" and isinstance(item.get("evidence_ids"), list):
                prior_ids = [int(v) for v in item["evidence_ids"] if str(v).isdigit()]
                if prior_ids:
                    break

    if effective_intent in ("tasks", "overdue_tasks"):
        tasks = (db.query(Task).filter(Task.is_completed.is_(False))
                 .order_by(Task.due_at.asc().nullslast(), Task.created_at.desc()).limit(100).all())
        result = []
        for task in tasks:
            due = _to_aware(task.due_at)
            overdue = bool(due is not None and due < now_utc)
            if effective_intent == "overdue_tasks" and not overdue:
                continue
            result.append({"task_id": task.id, "title": task.title, "description": task.description,
                           "due_at": due.isoformat() if due else None, "overdue": overdue})
        result = result[:limit]
        label = "overdue pending task(s)" if effective_intent == "overdue_tasks" else "pending task(s)"
        answer = f"I found {len(result)} {label}." + ("\n" + "\n".join(
            f"- {x['title']}" + (f" — due {x['due_at']}" if x["due_at"] else "") for x in result) if result else "")
        return {"question": question, "intent": effective_intent, "answer": answer, "evidence": result,
                "evidence_ids": [], "matched_count": len(result), "matched_tasks": len(result),
                "matched_emails": 0, "provider": "local_database", "is_follow_up": is_followup,
                "write_actions_taken": False, "searched_records": len(result)}

    rows = (db.query(EmailMessage, EmailAnalysis)
            .outerjoin(EmailAnalysis, EmailAnalysis.email_id == EmailMessage.id)
            .order_by(EmailMessage.message_date.desc(), EmailMessage.id.desc()).limit(500).all())
    candidates: list[tuple[int, EmailMessage, EmailAnalysis | None, list[str]]] = []
    q_tokens = [t for t in re.findall(r"[a-z0-9@._+-]+", q) if len(t) > 2 and t not in STOPWORDS]
    asks_overdue_deadlines = any(x in q for x in ("overdue", "past due", "already due", "expired"))

    for email, analysis in rows:
        if prior_ids and is_followup and email.id not in prior_ids:
            continue
        text = _email_text(email, analysis)
        fallback = None if analysis else classify_email(email)
        category = analysis.category if analysis else fallback["category"]
        priority = analysis.priority if analysis else fallback["priority"]
        priority_score = analysis.priority_score if analysis else fallback["priority_score"]
        deadline = analysis.detected_deadline if analysis else fallback.get("detected_deadline")
        requires_response = analysis.requires_response if analysis else fallback["requires_response"]
        score, reasons = 0, []

        if effective_intent == "interviews":
            matched = [t for t in INTENT_TERMS["interviews"] if t in text]
            if not matched: continue
            score += 8 + min(len(matched), 3) * 2
            reasons.append("interview-related wording: " + ", ".join(matched[:3]))
        elif effective_intent in ("internships", "opportunities", "career", "jobs"):
            intern_match = any(t in text for t in INTENT_TERMS["internships"])
            job_match = any(t in text for t in INTENT_TERMS["jobs"]) or category == "JOB"
            stored_career_signal = bool(analysis and getattr(analysis, "career_signal", False))
            if effective_intent == "internships" and not (intern_match or (category == "JOB" and "intern" in text)): continue
            if effective_intent == "jobs" and not job_match: continue
            if effective_intent in ("opportunities", "career") and not (intern_match or job_match or stored_career_signal): continue
            score += (8 if intern_match else 6) + (min(sum(t in text for t in INTENT_TERMS["jobs"]), 4) * 2)
            if stored_career_signal:
                score += min(int(analysis.career_confidence or 0) // 30, 3)
                reasons.append(f"career-stage signal: {analysis.career_stage} (heuristic confidence {analysis.career_confidence}/100)")
            reasons.append("job or internship opportunity wording" if effective_intent in ("opportunities", "career") else ("internship-related wording" if effective_intent == "internships" else "job or hiring-related wording"))
        elif effective_intent == "deadlines":
            deadline_aware = (
            _to_aware(deadline)
            if isinstance(deadline, datetime)
            else None
            )
            matched = [
                    term for term in INTENT_TERMS["deadlines"]
                if term in text
                ]

            asks_internships = any(
                term in q
                for term in ("internship", "internships", "internshala", "intern role")
                )
            asks_jobs = any(
                    term in q
                    for term in ("job", "jobs", "career", "graduate trainee", "recruiter")
                    )
            asks_finance = any(
                    term in q
                    for term in ("finance", "payment", "invoice", "bill", "salary", "tax")
                )

            if asks_internships and not any(
                    term in text for term in INTENT_TERMS["internships"]
                ):
                    continue

            if asks_jobs and not any(
                term in text for term in INTENT_TERMS["jobs"]
            ):
                continue

            if asks_finance and category != "FINANCE" and not any(
                term in text for term in INTENT_TERMS["finance"]
            ):
                continue

            if asks_overdue_deadlines:
                if deadline_aware is None or deadline_aware >= now_utc:
                    continue
                score += 12
                reasons.append("stored deadline is overdue")

            elif any(term in q for term in ("upcoming", "future", "next")):
                if deadline_aware is None or deadline_aware <= now_utc:
                    continue
                score += 12
                reasons.append("stored upcoming deadline detected")

            elif deadline_aware is not None:
                score += 12
                reasons.append("stored deadline detected")

            elif matched:
                score += 5
                reasons.append("deadline wording found; no normalized date available")

            else:
                continue
        elif effective_intent == "urgent" or filter_priority:
            if priority not in ("HIGH", "URGENT") and int(priority_score or 0) < 70: continue
            score += int(priority_score or 0)
            reasons.append(f"priority signal: {priority}")
        elif effective_intent == "response":
            if not requires_response and not any(t in text for t in INTENT_TERMS["response"]): continue
            score += 8
            reasons.append("possible response or follow-up signal")
        elif effective_intent == "finance":
            matched = [t for t in INTENT_TERMS["finance"] if t in text]
            if not matched and category != "FINANCE": continue
            score += 8 + len(matched)
            reasons.append("finance-related wording")
        elif effective_intent == "promotions":
            if category != "PROMOTIONS" and not any(t in text for t in INTENT_TERMS["promotions"]): continue
            score += 5
            reasons.append("promotion-related wording")
        elif effective_intent == "recent":
            score += 1
            reasons.append("recent stored email")
        else:
            matched = [t for t in q_tokens if t in text]
            if not matched: continue
            score += len(matched) * 3
            reasons.append("matched question terms: " + ", ".join(matched[:5]))

        if analysis:
            score += min(int(analysis.priority_score or 0) // 30, 3)
        if is_followup and filter_priority and priority not in ("HIGH", "URGENT") and int(priority_score or 0) < 70:
            continue
        candidates.append((score, email, analysis, reasons))

    candidates.sort(key=lambda x: (x[0], _to_aware(x[1].message_date) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    chosen = candidates[:limit]
    evidence = []
    for score, email, analysis, reasons in chosen:
        item = _payload(email, analysis)
        item["match_score"] = score
        item["why_matched"] = "; ".join(reasons)
        # The legacy dashboard/tests use this field to distinguish career leads.
        if effective_intent in ("internships", "opportunities", "career", "jobs"):
            item["opportunity_stage"] = item.get("career_stage") if item.get("career_stage") not in (None, "NONE", "POSSIBLE_OPPORTUNITY") else "OPPORTUNITY"
        evidence.append(item)

    if evidence:
        labels = {"interviews": "interview-related", "internships": "internship opportunity",
                  "opportunities": "job and internship opportunity", "career": "job and internship opportunity",
                  "jobs": "job or career", "deadlines": "deadline-related", "urgent": "high-priority",
                  "response": "possible response-needed", "finance": "finance-related",
                  "promotions": "promotion-related", "recent": "recent"}
        label = labels.get(effective_intent, "relevant")
        answer = f"I found {len(evidence)} {label} email(s) in FRIDAY's stored records."
        for i, item in enumerate(evidence, 1):
            answer += f"\n{i}. {item['subject']} — {item['sender']} ({item['category']} / {item['priority']}). Why it matched: {item['why_matched']}."
            if item.get("detected_deadline"):
                answer += f" Detected deadline: {item['detected_deadline']} (verify against the original email)."
        answer += "\nThese matches use stored sender, subject, snippet, labels, and analysis metadata; full email bodies and attachments are not searched unless separately retrieved."
    else:
        if effective_intent == "interviews":
            detail = "No interview-related wording was found in the stored sender, subject, snippets, labels, or analysis metadata."
        elif effective_intent == "deadlines":
            detail = "No stored emails matched the deadline criteria in the fields FRIDAY can search."
        else:
            detail = f"No stored email matched the {effective_intent.replace('_', ' ')} query."
        answer = (f"I searched {len(rows)} stored email record(s). {detail} This does not prove Gmail contains no such message; it does not mean your Gmail inbox has no such messages: "
                  "full email bodies and live Gmail were not searched. Try syncing more recent messages or analyzing stored emails.")

    return {"question": question, "intent": effective_intent, "answer": answer, "evidence": evidence,
            "evidence_ids": [x["email_id"] for x in evidence], "matched_count": len(evidence),
            "matched_emails": len(evidence), "matched_tasks": 0, "provider": "local_evidence",
            "is_follow_up": is_followup, "write_actions_taken": False, "searched_records": len(rows)}


def answer_from_stored_data(
    db: Session,
    question: str,
    history: list[dict[str, Any]] | None = None,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Backward-compatible interface; ``now`` is optional for deterministic tests."""
    result = answer_assistant_question(
        db, question, history=history, limit=limit, now=now
    )
    # Preserve the legacy intent label expected by older callers while the current
    # conversational endpoint uses the clearer "career" intent.
    if result.get("intent") == "career":
        result["intent"] = "opportunities"
    # Keep both the newer API fields and the established legacy response contract.
    result.setdefault("matched_emails", len(result.get("evidence", [])) if result.get("provider") == "local_evidence" else 0)
    result.setdefault("matched_tasks", len(result.get("evidence", [])) if result.get("provider") == "local_database" else 0)
    return result
