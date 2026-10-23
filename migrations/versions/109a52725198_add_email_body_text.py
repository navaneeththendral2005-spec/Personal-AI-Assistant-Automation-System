"""add email body text

Revision ID: 109a52725198
Revises: friday_career_action_06
Create Date: 2026-10-23 14:08:13.767485

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '109a52725198'
down_revision: Union[str, Sequence[str], None] = 'friday_career_action_06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "emails",
        sa.Column("body_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("emails", "body_text")
