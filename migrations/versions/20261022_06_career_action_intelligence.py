"""Add explainable career-stage and action evidence to email analyses.

Revision ID: friday_career_action_06
Revises: friday_email_conf_05
"""
from alembic import op
import sqlalchemy as sa

revision = "friday_career_action_06"
down_revision = "friday_email_conf_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_analyses", sa.Column("action_evidence", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("email_analyses", sa.Column("career_signal", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("email_analyses", sa.Column("career_stage", sa.String(length=24), nullable=False, server_default="NONE"))
    op.add_column("email_analyses", sa.Column("career_confidence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_analyses", sa.Column("career_evidence", sa.JSON(), nullable=False, server_default="[]"))
    op.create_index("ix_email_analyses_career_signal", "email_analyses", ["career_signal"])
    op.create_index("ix_email_analyses_career_stage", "email_analyses", ["career_stage"])


def downgrade() -> None:
    op.drop_index("ix_email_analyses_career_stage", table_name="email_analyses")
    op.drop_index("ix_email_analyses_career_signal", table_name="email_analyses")
    op.drop_column("email_analyses", "career_evidence")
    op.drop_column("email_analyses", "career_confidence")
    op.drop_column("email_analyses", "career_stage")
    op.drop_column("email_analyses", "career_signal")
    op.drop_column("email_analyses", "action_evidence")
