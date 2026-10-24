"""Record the production classifier method and version on each analysis.

Revision ID: friday_classifier_meta_07
Revises: 109a52725198
"""
from alembic import op
import sqlalchemy as sa

revision = "friday_classifier_meta_07"
down_revision = "109a52725198"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_analyses",
        sa.Column("classifier_method", sa.String(length=32), nullable=False, server_default="rules"),
    )
    op.add_column(
        "email_analyses",
        sa.Column("classifier_version", sa.String(length=64), nullable=False, server_default="friday-rules-v1"),
    )


def downgrade() -> None:
    op.drop_column("email_analyses", "classifier_version")
    op.drop_column("email_analyses", "classifier_method")
