"""Local reminder poller.

The first version logs due reminders to the backend terminal. It does not send
email, Telegram messages, or speech; those delivery channels can be added later.
"""
import logging
from datetime import datetime, timezone
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from backend.database import SessionLocal
from backend.models import Task

logger = logging.getLogger("friday.reminders")
_scheduler: BackgroundScheduler | None = None
_scheduler_lock = Lock()


def check_due_reminders() -> int:
    """Log due reminders once and mark them as notified in the database."""
    db = SessionLocal()
    sent_count = 0
    now = datetime.now(timezone.utc)
    try:
        due_tasks = list(
            db.scalars(
                select(Task)
                .where(
                    Task.is_completed.is_(False),
                    Task.due_at.is_not(None),
                    Task.due_at <= now,
                    Task.reminder_sent.is_(False),
                )
                .order_by(Task.due_at.asc(), Task.id.asc())
                .limit(100)
                .with_for_update(skip_locked=True)
            ).all()
        )
        for task in due_tasks:
            # Console logging is the initial notification channel.
            logger.warning("FRIDAY REMINDER | task_id=%s | title=%s | due_at=%s", task.id, task.title, task.due_at)
            task.reminder_sent = True
            task.updated_at = now
            sent_count += 1
        if sent_count:
            db.commit()
        return sent_count
    except Exception:
        db.rollback()
        logger.exception("Reminder polling failed")
        return 0
    finally:
        db.close()


def start_reminder_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            return
        scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
        scheduler.add_job(
            check_due_reminders,
            trigger="interval",
            seconds=30,
            id="friday_due_reminder_poll",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info("FRIDAY local reminder scheduler started (30-second polling interval).")


def stop_reminder_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=False)
        _scheduler = None
