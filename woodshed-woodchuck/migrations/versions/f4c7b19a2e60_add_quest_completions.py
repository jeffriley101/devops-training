"""add persistent quest completions

Revision ID: f4c7b19a2e60
Revises: d91f6a7b2c40
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "f4c7b19a2e60"
down_revision = "d91f6a7b2c40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quest_completions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("quest_id", sa.String(length=100), nullable=False),
        sa.Column("logged_minutes", sa.Integer(), nullable=False),
        sa.Column("reward_amount", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "logged_minutes > 0", name="ck_quest_completion_minutes_positive"
        ),
        sa.CheckConstraint(
            "reward_amount > 0", name="ck_quest_completion_reward_positive"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "activity_date",
            name="uq_quest_completion_profile_date",
        ),
    )
    op.create_index(
        op.f("ix_quest_completions_profile_id"),
        "quest_completions",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_quest_completions_activity_date"),
        "quest_completions",
        ["activity_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_quest_completions_activity_date"),
        table_name="quest_completions",
    )
    op.drop_index(
        op.f("ix_quest_completions_profile_id"),
        table_name="quest_completions",
    )
    op.drop_table("quest_completions")
