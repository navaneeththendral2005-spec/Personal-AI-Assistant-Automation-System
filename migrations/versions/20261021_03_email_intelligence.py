"""Additive email intelligence analysis table.

Revision ID: friday_email_intel_03
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = "friday_email_intel_03"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_id", sa.Integer(), sa.ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="OTHER"),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="NORMAL"),
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("classification_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("requires_response", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("detected_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("analysis_status", sa.String(length=16), nullable=False, server_default="complete"),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_analyses_email_id", "email_analyses", ["email_id"], unique=False)
    op.create_index("ix_email_analyses_category", "email_analyses", ["category"], unique=False)
    op.create_index("ix_email_analyses_priority", "email_analyses", ["priority"], unique=False)
    op.create_index("ix_email_analyses_detected_deadline", "email_analyses", ["detected_deadline"], unique=False)
    op.create_index("ix_email_analyses_analysis_status", "email_analyses", ["analysis_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_email_analyses_analysis_status", table_name="email_analyses")
    op.drop_index("ix_email_analyses_detected_deadline", table_name="email_analyses")
    op.drop_index("ix_email_analyses_priority", table_name="email_analyses")
    op.drop_index("ix_email_analyses_category", table_name="email_analyses")
    op.drop_index("ix_email_analyses_email_id", table_name="email_analyses")
    op.drop_table("email_analyses")
