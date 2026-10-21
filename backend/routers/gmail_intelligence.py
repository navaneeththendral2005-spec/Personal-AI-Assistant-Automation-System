"""FRIDAY email intelligence endpoints. Intended for the local/dev API."""
import logging
from collections.abc import Generator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.schemas_email_intelligence import AskInboxRequest, CreateTaskFromEmailRequest
from backend.services.email_intelligence_service import (
    analyze_stored_emails,
    answer_inbox_question,
    create_task_from_email,
    get_email_analysis,
    get_insights,
    list_analyzed_emails,
    summarize_inbox,
)

logger = logging.getLogger("friday.email_intelligence")
router = APIRouter(prefix="/gmail/intelligence", tags=["Email Intelligence"])


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/analyze")
def analyze_emails(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    """Classify and score already-synced email metadata; does not modify Gmail."""
    try:
        return analyze_stored_emails(db, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback(); logger.exception("Email analysis failed")
        raise HTTPException(status_code=500, detail="Unable to analyze stored emails.") from exc


@router.post("/sync-and-analyze")
def sync_and_analyze(max_results: int = Query(default=10, ge=1, le=50), db: Session = Depends(get_db)):
    """Sync recent Gmail metadata, then classify the newest stored messages."""
    try:
        from backend.services.gmail_sync_service import sync_gmail_messages
        sync_result = sync_gmail_messages(db, max_results=max_results)
        analysis_result = analyze_stored_emails(db, limit=max_results)
        return {"sync": sync_result, "analysis": analysis_result}
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback(); logger.exception("Sync and analyze failed")
        raise HTTPException(status_code=502, detail="Gmail sync or email analysis failed. Check server logs.") from exc


@router.get("/emails")
def emails(
    category: str | None = Query(default=None, pattern="^(JOB|FINANCE|PERSONAL|PROMOTIONS|UPDATES|OTHER|job|finance|personal|promotions|updates|other)$"),
    priority: str | None = Query(default=None, pattern="^(LOW|NORMAL|HIGH|URGENT|low|normal|high|urgent)$"),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    requires_response: bool | None = None,
    min_confidence: int | None = Query(default=None, ge=0, le=100),
    career_only: bool = False,
    career_stage: str | None = Query(default=None, max_length=24),
    has_deadline: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        return list_analyzed_emails(db, category=category, priority=priority, search=search,
                                    requires_response=requires_response, min_confidence=min_confidence,
                                    career_only=career_only, career_stage=career_stage, has_deadline=has_deadline,
                                    limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Email intelligence query failed")
        raise HTTPException(status_code=500, detail="Unable to retrieve analyzed emails.") from exc


@router.get("/emails/{email_id}")
def email_analysis(email_id: int, db: Session = Depends(get_db)):
    result = get_email_analysis(db, email_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Email analysis not found. Run POST /gmail/intelligence/analyze first.")
    return result


@router.get("/insights")
def insights(db: Session = Depends(get_db)):
    try: return get_insights(db)
    except SQLAlchemyError as exc:
        logger.exception("Email insights failed")
        raise HTTPException(status_code=500, detail="Unable to calculate email insights.") from exc


@router.get("/summary")
def inbox_summary(limit: int = Query(default=10, ge=1, le=50), use_ai: bool = True, db: Session = Depends(get_db)):
    try: return summarize_inbox(db, limit=limit, use_ai=use_ai)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Inbox summary failed")
        raise HTTPException(status_code=500, detail="Unable to summarize stored emails.") from exc


@router.post("/ask")
def ask_inbox(payload: AskInboxRequest, db: Session = Depends(get_db)):
    try: return answer_inbox_question(db, payload.question, limit=payload.limit)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Inbox question failed")
        raise HTTPException(status_code=500, detail="Unable to answer from stored emails.") from exc


@router.post("/emails/{email_id}/create-task", status_code=status.HTTP_201_CREATED)
def task_from_email(email_id: int, payload: CreateTaskFromEmailRequest, db: Session = Depends(get_db)):
    """Explicitly create a task from a selected email; no task is created automatically."""
    try:
        task = create_task_from_email(db, email_id, title=payload.title, due_at=payload.due_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback(); logger.exception("Task creation from email failed")
        raise HTTPException(status_code=500, detail="Unable to create task from email.") from exc
    if task is None: raise HTTPException(status_code=404, detail="Stored email not found.")
    return task
