"""Add task reminder tracking and update timestamps.

Revision ID: friday_task_reminders_02
Revises: e1f4382c7e3c
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "friday_task_reminders_02"
down_revision = "e1f4382c7e3c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("reminder_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_tasks_reminder_sent", "tasks", ["reminder_sent"], unique=False)
    op.execute("UPDATE tasks SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("tasks", "updated_at", nullable=False)
    op.alter_column("tasks", "reminder_sent", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_tasks_reminder_sent", table_name="tasks")
    op.drop_column("tasks", "reminder_sent")
    op.drop_column("tasks", "updated_at")
