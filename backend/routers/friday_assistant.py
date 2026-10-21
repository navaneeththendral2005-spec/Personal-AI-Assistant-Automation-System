"""A small unified briefing endpoint combining email intelligence and existing tasks."""
import logging
from collections.abc import Generator
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import Task
from backend.models_email_intelligence import EmailAnalysis
from backend.services.email_intelligence_service import list_analyzed_emails
from backend.services.assistant_query_service import answer_assistant_question

logger = logging.getLogger("friday.assistant")
router = APIRouter(prefix="/assistant", tags=["FRIDAY Assistant"])


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/briefing")
def daily_briefing(db: Session = Depends(get_db)):
    """Combine high-priority analyzed emails and pending tasks into a daily briefing."""
    try:
        email_result = list_analyzed_emails(db, priority="URGENT", limit=10, offset=0)
        high_result = list_analyzed_emails(db, priority="HIGH", limit=10, offset=0)
        response_result = list_analyzed_emails(db, requires_response=True, limit=10, offset=0)
        all_recent = list_analyzed_emails(db, limit=50, offset=0)["emails"]
        deadline_emails = [item for item in all_recent if item.get("detected_deadline") is not None]
        deadline_emails.sort(key=lambda item: item["detected_deadline"])
        now = datetime.now(timezone.utc)
        tasks = db.query(Task).filter(Task.is_completed.is_(False)).order_by(Task.due_at.asc().nullslast(), Task.created_at.desc()).limit(20).all()
        task_items = []
        for task in tasks:
            due_at = task.due_at
            if due_at is not None and due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            overdue = bool(due_at and due_at < now)
            task_items.append({"id": task.id, "title": task.title, "due_at": due_at, "overdue": overdue})
        return {
            "generated_at": now,
            "message": "Review urgent email signals first, then high-priority emails and pending tasks.",
            "urgent_emails": email_result["emails"],
            "high_priority_emails": high_result["emails"],
            "emails_may_need_response": response_result["emails"],
            "emails_with_detected_deadlines": deadline_emails[:10],
            "pending_tasks": task_items,
            "note": "Priority, response, and deadline items are heuristic suggestions based on stored metadata/snippets. Verify against the original email. This briefing does not create tasks or send messages.",
        }
    except SQLAlchemyError as exc:
        logger.exception("Daily briefing failed")
        raise HTTPException(status_code=500, detail="Unable to generate FRIDAY briefing.") from exc


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[int] = Field(default_factory=list, max_length=50)


class AssistantChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question cannot be blank.")
        return value


@router.post("/chat")
def assistant_chat(payload: AssistantChatRequest, db: Session = Depends(get_db)):
    """Answer from stored email/task data. Chat is read-only; no external actions are executed."""
    try:
        history = [turn.model_dump() for turn in payload.history]
        result = answer_assistant_question(db, payload.question, history=history, limit=20)
        return {
            **result,
            "write_actions_taken": False,
            "note": "Read-only answer based on FRIDAY's stored records. Email text is treated as evidence, not instructions. Verify decisions against original messages.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Assistant chat failed")
        raise HTTPException(status_code=500, detail="Unable to answer from FRIDAY's stored data.") from exc
