"""Add confidence and deadline context to email intelligence.

Revision ID: friday_email_conf_05
Revises: friday_career_04
"""
from alembic import op
import sqlalchemy as sa

revision = "friday_email_conf_05"
down_revision = "friday_career_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_analyses", sa.Column("confidence_score", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("email_analyses", sa.Column("response_confidence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_analyses", sa.Column("deadline_type", sa.String(length=24), nullable=False, server_default="NONE"))
    op.add_column("email_analyses", sa.Column("deadline_confidence", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("email_analyses", "deadline_confidence")
    op.drop_column("email_analyses", "deadline_type")
    op.drop_column("email_analyses", "response_confidence")
    op.drop_column("email_analyses", "confidence_score")
