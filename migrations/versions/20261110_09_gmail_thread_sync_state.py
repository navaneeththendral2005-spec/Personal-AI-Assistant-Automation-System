"""Track verification coverage for locally known Gmail conversation threads.

Revision ID: friday_thread_sync_09
Revises: friday_schema_align_08
"""
from alembic import op
import sqlalchemy as sa

revision = "friday_thread_sync_09"
down_revision = "friday_schema_align_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_thread_sync_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=False),
        sa.Column("sync_status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_type", sa.String(length=100), nullable=True),
        sa.UniqueConstraint("gmail_thread_id", name="uq_gmail_thread_sync_states_thread_id"),
    )
    op.create_index("ix_gmail_thread_sync_states_gmail_thread_id", "gmail_thread_sync_states", ["gmail_thread_id"])
    op.create_index("ix_gmail_thread_sync_states_sync_status", "gmail_thread_sync_states", ["sync_status"])


def downgrade() -> None:
    op.drop_index("ix_gmail_thread_sync_states_sync_status", table_name="gmail_thread_sync_states")
    op.drop_index("ix_gmail_thread_sync_states_gmail_thread_id", table_name="gmail_thread_sync_states")
    op.drop_table("gmail_thread_sync_states")
