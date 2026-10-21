from datetime import datetime
import pytest
from pydantic import ValidationError

from backend.schemas import TaskCreate, TaskUpdate


def test_task_create_strips_title():
    task = TaskCreate(title="  Call the dentist  ")
    assert task.title == "Call the dentist"


def test_task_create_rejects_blank_title():
    with pytest.raises(ValidationError):
        TaskCreate(title="   ")


def test_due_at_requires_timezone():
    with pytest.raises(ValidationError):
        TaskCreate(title="Timezone test", due_at=datetime(2026, 10, 21, 9, 0))


def test_task_update_requires_field():
    with pytest.raises(ValidationError):
        TaskUpdate()


def test_task_update_accepts_completion_flag():
    update = TaskUpdate(is_completed=True)
    assert update.is_completed is True
