"""add daily trivia attempts

Revision ID: d91f6a7b2c40
Revises: c34e8f1a7d45
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "d91f6a7b2c40"
down_revision = "c34e8f1a7d45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_trivia_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("selected_answer", sa.String(length=200), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "activity_date",
            name="uq_daily_trivia_attempt_profile_date",
        ),
    )
    op.create_index(
        op.f("ix_daily_trivia_attempts_profile_id"),
        "daily_trivia_attempts",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_daily_trivia_attempts_activity_date"),
        "daily_trivia_attempts",
        ["activity_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_daily_trivia_attempts_activity_date"),
        table_name="daily_trivia_attempts",
    )
    op.drop_index(
        op.f("ix_daily_trivia_attempts_profile_id"),
        table_name="daily_trivia_attempts",
    )
    op.drop_table("daily_trivia_attempts")
