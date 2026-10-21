from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4
from pathlib import Path
import logging
from collections.abc import Generator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.database import SessionLocal, engine
from backend.models import Task
from backend.schemas import TaskCreate, TaskRead, TaskUpdate
from backend.services.task_service import (
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)
from backend.services.reminder_service import start_reminder_scheduler, stop_reminder_scheduler
from backend.routers.gmail import router as gmail_router
from backend.routers.gmail_sync_router import router as gmail_sync_router
from backend.routers.gmail_intelligence import router as gmail_intelligence_router
from backend.routers.friday_assistant import router as friday_assistant_router
from backend.routers.career import router as career_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("friday")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop the local reminder poller with the API process."""
    start_reminder_scheduler()
    try:
        yield
    finally:
        stop_reminder_scheduler()


app = FastAPI(
    title="FRIDAY Personal Assistant API",
    description="Task management and local due-time reminders for FRIDAY.",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(gmail_router)
app.include_router(gmail_sync_router)
app.include_router(gmail_intelligence_router)
app.include_router(friday_assistant_router)
app.include_router(career_router)

# Lightweight production hygiene: request IDs, duration logging, and browser security headers.
# This does not replace authentication; keep the API bound to localhost unless protected.
@app.middleware("http")
async def request_hardening(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid4()))[:100]
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error | request_id=%s | path=%s", request_id, request.url.path)
        raise
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    logger.info("request_id=%s method=%s path=%s status=%s duration_ms=%s",
                request_id, request.method, request.url.path, response.status_code, elapsed_ms)
    return response


DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
app.mount("/dashboard-assets", StaticFiles(directory=str(DASHBOARD_DIR)), name="dashboard-assets")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    """Serve the local FRIDAY dashboard."""
    return FileResponse(DASHBOARD_DIR / "index.html", media_type="text/html")

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/", tags=["System"])
def home() -> dict[str, str]:
    return {
        "assistant": "FRIDAY",
        "status": "online",
        "message": "FRIDAY is ready.",
        "docs": "/docs",
    }


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "healthy", "service": "friday-backend", "database": "connected"}
    except SQLAlchemyError as exc:
        logger.warning("Database health check failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "database": "disconnected"},
        ) from exc


@app.post("/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED, tags=["Tasks"])
def create_task_endpoint(payload: TaskCreate, db: Session = Depends(get_db)) -> Task:
    try:
        return create_task(db, payload)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to create task")
        raise HTTPException(status_code=500, detail="Unable to create task.") from exc


@app.get("/tasks", response_model=list[TaskRead], tags=["Tasks"])
def list_tasks_endpoint(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(pending|completed)$"),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    due_before: str | None = Query(default=None, description="ISO-8601 datetime, e.g. 2026-10-21T09:00:00+05:30"),
    due_after: str | None = Query(default=None, description="ISO-8601 datetime, e.g. 2026-10-20T00:00:00+05:30"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Task]:
    from datetime import datetime

    def parse_datetime(value: str | None, field: str):
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{field} must be a valid ISO-8601 datetime.") from exc
        if parsed.tzinfo is None:
            raise HTTPException(status_code=422, detail=f"{field} must include a timezone offset.")
        return parsed

    try:
        return list_tasks(
            db,
            status_filter=status_filter,
            search=search,
            due_before=parse_datetime(due_before, "due_before"),
            due_after=parse_datetime(due_after, "due_after"),
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError as exc:
        logger.exception("Failed to list tasks")
        raise HTTPException(status_code=500, detail="Unable to retrieve tasks.") from exc


@app.get("/tasks/{task_id}", response_model=TaskRead, tags=["Tasks"])
def get_task_endpoint(task_id: int, db: Session = Depends(get_db)) -> Task:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.patch("/tasks/{task_id}", response_model=TaskRead, tags=["Tasks"])
def update_task_endpoint(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)) -> Task:
    try:
        task = update_task(db, task_id, payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to update task")
        raise HTTPException(status_code=500, detail="Unable to update task.") from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.post("/tasks/{task_id}/complete", response_model=TaskRead, tags=["Tasks"])
def complete_task_endpoint(task_id: int, db: Session = Depends(get_db)) -> Task:
    try:
        task = complete_task(db, task_id)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to complete task")
        raise HTTPException(status_code=500, detail="Unable to complete task.") from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.post("/tasks/{task_id}/reopen", response_model=TaskRead, tags=["Tasks"])
def reopen_task_endpoint(task_id: int, db: Session = Depends(get_db)) -> Task:
    try:
        task = update_task(db, task_id, TaskUpdate(is_completed=False))
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to reopen task")
        raise HTTPException(status_code=500, detail="Unable to reopen task.") from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Tasks"])
def delete_task_endpoint(task_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        deleted = delete_task(db, task_id)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to delete task")
        raise HTTPException(status_code=500, detail="Unable to delete task.") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
