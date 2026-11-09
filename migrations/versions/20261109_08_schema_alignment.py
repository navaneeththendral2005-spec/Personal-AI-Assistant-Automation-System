"""Add missing task lookup indexes for schema/model alignment.

Revision ID: friday_schema_align_08
Revises: friday_classifier_meta_07

This migration is additive: it creates indexes only and does not alter or
rewrite existing task, email, or analysis records.
"""
from alembic import op

revision = "friday_schema_align_08"
down_revision = "friday_classifier_meta_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_tasks_title", "tasks", ["title"], unique=False)
    op.create_index("ix_tasks_is_completed", "tasks", ["is_completed"], unique=False)
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    op.drop_index("ix_tasks_is_completed", table_name="tasks")
    op.drop_index("ix_tasks_title", table_name="tasks")
