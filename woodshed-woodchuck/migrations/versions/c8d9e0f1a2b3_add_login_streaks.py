"""add durable daily login streaks

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_streaks",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column(
            "current_days",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_login_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.CheckConstraint(
            "current_days >= 0",
            name="ck_login_streak_current_days",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["woodchuck_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("profile_id"),
    )


def downgrade() -> None:
    op.drop_table("login_streaks")
