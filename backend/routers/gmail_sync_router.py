"""Additive Gmail sync routes; existing /gmail endpoints are not replaced."""
from collections.abc import Generator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.gmail_sync_schemas import (
    StoredEmailListResponse,
    StoredEmailRead,
)
from backend.services.gmail_sync_service import (
    list_stored_emails,
    sync_gmail_messages,
)

router = APIRouter(prefix="/gmail", tags=["Gmail Synchronization"])


def get_sync_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/sync", summary="Synchronize recent Gmail metadata into PostgreSQL")
def sync_gmail_endpoint(
    max_results: int = Query(default=10, ge=1, le=500),
    db: Session = Depends(get_sync_db),
):
    """Fetch up to 500 recent messages across 50-message Gmail API pages."""
    try:
        return sync_gmail_messages(db, max_results=max_results)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Gmail OAuth credentials are not configured. Check the local secrets folder.",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail="Gmail synchronization failed while saving to the database; the batch was rolled back.",
        ) from exc
    except Exception as exc:
        # Avoid returning Google/client internals or message content to callers.
        raise HTTPException(
            status_code=502,
            detail="Unable to fetch Gmail messages. Check local logs for details.",
        ) from exc


@router.get(
    "/stored-emails",
    response_model=StoredEmailListResponse,
    summary="List emails already stored in PostgreSQL",
)
def stored_emails_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_sync_db),
):
    try:
        emails = list_stored_emails(db, limit=limit, offset=offset)
        return {
            "count": len(emails),
            "limit": limit,
            "offset": offset,
            "emails": emails,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve stored emails.",
        ) from exc
