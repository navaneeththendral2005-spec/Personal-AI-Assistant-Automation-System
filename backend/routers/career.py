"""Career opportunity tracker APIs; status updates are explicit user actions."""
from collections.abc import Generator
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.services.career_service import career_summary, list_follow_ups, list_opportunities, sync_opportunities, update_opportunity

router = APIRouter(prefix="/career", tags=["Career Tracker"])

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class OpportunityUpdate(BaseModel):
    status: Literal["DISCOVERED", "INTERESTED", "APPLIED", "INTERVIEWING", "OFFER", "REJECTED", "ARCHIVED"] | None = None
    follow_up_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)

@router.post("/opportunities/sync")
def sync_career_opportunities(limit: int = Query(default=200, ge=1, le=500), db: Session = Depends(get_db)):
    try:
        return sync_opportunities(db, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to synchronize career opportunities.") from exc

@router.get("/opportunities")
def get_career_opportunities(status: str | None = None, type: str | None = None, limit: int = Query(default=100, ge=1, le=200), db: Session = Depends(get_db)):
    try:
        items = list_opportunities(db, status=status, opportunity_type=type, limit=limit)
        return {"count": len(items), "opportunities": items}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail="Unable to retrieve career opportunities.") from exc

@router.patch("/opportunities/{opportunity_id}")
def patch_career_opportunity(opportunity_id: int, payload: OpportunityUpdate, db: Session = Depends(get_db)):
    try:
        result = update_opportunity(db, opportunity_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to update career opportunity.") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Career opportunity not found.")
    return result


@router.get("/summary")
def get_career_summary(db: Session = Depends(get_db)):
    try:
        return career_summary(db)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail="Unable to calculate career pipeline summary.") from exc


@router.get("/follow-ups")
def get_career_follow_ups(
    window_days: int = Query(default=7, ge=0, le=90),
    include_overdue: bool = True,
    db: Session = Depends(get_db),
):
    try:
        items = list_follow_ups(db, window_days=window_days, include_overdue=include_overdue)
        return {"count": len(items), "window_days": window_days, "follow_ups": items,
                "write_actions_taken": False}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail="Unable to retrieve career follow-ups.") from exc
