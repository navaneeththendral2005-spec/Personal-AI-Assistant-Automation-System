from datetime import datetime, timezone

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from backend.models import Task
from backend.schemas import TaskCreate, TaskUpdate


def create_task(db: Session, payload: TaskCreate) -> Task:
    task = Task(
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
        is_completed=False,
        reminder_sent=False,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def list_tasks(
    db: Session,
    *,
    status_filter: str | None = None,
    search: str | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Task]:
    statement = select(Task)
    if status_filter == "pending":
        statement = statement.where(Task.is_completed.is_(False))
    elif status_filter == "completed":
        statement = statement.where(Task.is_completed.is_(True))
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(or_(Task.title.ilike(pattern), Task.description.ilike(pattern)))
    if due_before is not None:
        statement = statement.where(Task.due_at.is_not(None), Task.due_at <= due_before)
    if due_after is not None:
        statement = statement.where(Task.due_at.is_not(None), Task.due_at >= due_after)
    statement = statement.order_by(Task.is_completed.asc(), Task.due_at.asc().nulls_last(), Task.id.desc())
    statement = statement.limit(limit).offset(offset)
    return list(db.scalars(statement).all())


def get_task(db: Session, task_id: int) -> Task | None:
    return db.get(Task, task_id)


def update_task(db: Session, task_id: int, payload: TaskUpdate) -> Task | None:
    task = db.get(Task, task_id)
    if task is None:
        return None

    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes and changes["title"] is None:
        raise ValueError("title cannot be null.")
    if "is_completed" in changes and changes["is_completed"] is None:
        raise ValueError("is_completed cannot be null.")

    for field, value in changes.items():
        setattr(task, field, value)

    # A newly assigned due time should trigger a fresh reminder.
    if "due_at" in changes:
        task.reminder_sent = False
    if changes.get("is_completed") is True:
        task.reminder_sent = True
    elif changes.get("is_completed") is False and task.due_at is not None:
        task.reminder_sent = False

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


def complete_task(db: Session, task_id: int) -> Task | None:
    task = db.get(Task, task_id)
    if task is None:
        return None
    task.is_completed = True
    task.reminder_sent = True
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task_id: int) -> bool:
    task = db.get(Task, task_id)
    if task is None:
        return False
    db.delete(task)
    db.commit()
    return True
