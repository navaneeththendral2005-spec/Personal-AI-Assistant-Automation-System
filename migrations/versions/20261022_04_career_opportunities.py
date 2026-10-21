"""Additive career opportunity tracker linked to stored email records.

Revision ID: friday_career_04
Revises: friday_email_intel_03
"""
from alembic import op
import sqlalchemy as sa

revision = "friday_career_04"
down_revision = "friday_email_intel_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "career_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_id", sa.Integer(), sa.ForeignKey("emails.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("role_title", sa.String(length=300), nullable=True),
        sa.Column("opportunity_type", sa.String(length=24), nullable=False, server_default="JOB"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="DISCOVERED"),
        sa.Column("application_url", sa.Text(), nullable=True),
        sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("email_id", name="uq_career_opportunities_email_id"),
    )
    op.create_index("ix_career_opportunities_email_id", "career_opportunities", ["email_id"])
    op.create_index("ix_career_opportunities_company", "career_opportunities", ["company"])
    op.create_index("ix_career_opportunities_opportunity_type", "career_opportunities", ["opportunity_type"])
    op.create_index("ix_career_opportunities_status", "career_opportunities", ["status"])
    op.create_index("ix_career_opportunities_follow_up_at", "career_opportunities", ["follow_up_at"])


def downgrade() -> None:
    op.drop_index("ix_career_opportunities_follow_up_at", table_name="career_opportunities")
    op.drop_index("ix_career_opportunities_status", table_name="career_opportunities")
    op.drop_index("ix_career_opportunities_opportunity_type", table_name="career_opportunities")
    op.drop_index("ix_career_opportunities_company", table_name="career_opportunities")
    op.drop_index("ix_career_opportunities_email_id", table_name="career_opportunities")
    op.drop_table("career_opportunities")
