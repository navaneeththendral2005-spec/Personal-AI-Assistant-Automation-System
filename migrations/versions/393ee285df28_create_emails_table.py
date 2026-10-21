"""Create emails table."""

from alembic import op
import sqlalchemy as sa


# Replace this with the revision ID in the generated file.
revision = "a1b2c3d4e5f6"

down_revision = "friday_task_reminders_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "emails",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "gmail_message_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("sender", sa.Text(), nullable=True),
        sa.Column("recipient", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column(
            "message_date",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "gmail_message_id",
            name="uq_emails_gmail_message_id",
        ),
    )

    op.create_index(
        "ix_emails_id",
        "emails",
        ["id"],
        unique=False,
    )

    op.create_index(
        "ix_emails_gmail_message_id",
        "emails",
        ["gmail_message_id"],
        unique=False,
    )

    op.create_index(
        "ix_emails_thread_id",
        "emails",
        ["thread_id"],
        unique=False,
    )

    op.create_index(
        "ix_emails_message_date",
        "emails",
        ["message_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_emails_message_date", table_name="emails")
    op.drop_index("ix_emails_thread_id", table_name="emails")
    op.drop_index(
        "ix_emails_gmail_message_id",
        table_name="emails",
    )
    op.drop_index("ix_emails_id", table_name="emails")
    op.drop_table("emails")