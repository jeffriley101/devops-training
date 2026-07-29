"""add contest persistence foundation

Revision ID: 8a899a61d621
Revises: 71263ef351b4
Create Date: 2026-07-28 14:14:45.436739

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a899a61d621"
down_revision: Union[str, Sequence[str], None] = "71263ef351b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("metric_type", sa.String(length=30), nullable=False),
        sa.Column("subject_type", sa.String(length=30), nullable=False),
        sa.Column("crown_category", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "metric_type IN ('practice_minutes', 'points')",
            name="ck_contest_metric_type",
        ),
        sa.CheckConstraint(
            "subject_type IN ('student', 'instrument')",
            name="ck_contest_subject_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_contests_crown_category"),
        "contests",
        ["crown_category"],
        unique=False,
    )
    op.create_index(op.f("ix_contests_key"), "contests", ["key"], unique=True)

    op.create_table(
        "seasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="America/Chicago",
            nullable=False,
        ),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'closed')",
            name="ck_season_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_seasons_key"), "seasons", ["key"], unique=True)

    op.create_table(
        "contest_weeks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column(
            "verification_deadline_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("finalize_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'pending', 'finalized')",
            name="ck_contest_week_status",
        ),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season_id",
            "week_start",
            name="uq_contest_week_season_start",
        ),
    )
    op.create_index(
        op.f("ix_contest_weeks_season_id"),
        "contest_weeks",
        ["season_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contest_weeks_week_start"),
        "contest_weeks",
        ["week_start"],
        unique=False,
    )

    op.create_table(
        "crown_progress",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("category_key", sa.String(length=100), nullable=False),
        sa.Column("qualifying_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("crown_earned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "category_key",
            name="uq_crown_progress_profile_category",
        ),
    )
    op.create_index(
        op.f("ix_crown_progress_category_key"),
        "crown_progress",
        ["category_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crown_progress_profile_id"),
        "crown_progress",
        ["profile_id"],
        unique=False,
    )

    op.create_table(
        "contest_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contest_week_id", sa.Integer(), nullable=False),
        sa.Column("contest_id", sa.Integer(), nullable=False),
        sa.Column("division", sa.String(length=100), nullable=False),
        sa.Column("subject_type", sa.String(length=30), nullable=False),
        sa.Column("subject_key", sa.String(length=100), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("instrument", sa.String(length=50), nullable=True),
        sa.Column("display_name_snapshot", sa.String(length=100), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("medal", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "division IN ('open', 'verified')",
            name="ck_contest_result_division",
        ),
        sa.CheckConstraint(
            "subject_type IN ('student', 'instrument')",
            name="ck_contest_result_subject_type",
        ),
        sa.CheckConstraint(
            "medal IN ('gold', 'silver', 'bronze')",
            name="ck_contest_result_medal",
        ),
        sa.ForeignKeyConstraint(["contest_id"], ["contests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["contest_week_id"], ["contest_weeks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contest_week_id",
            "contest_id",
            "division",
            "subject_key",
            name="uq_contest_result_week_contest_division_subject",
        ),
    )
    for column in (
        "contest_id",
        "contest_week_id",
        "division",
        "profile_id",
        "subject_key",
    ):
        op.create_index(
            op.f(f"ix_contest_results_{column}"),
            "contest_results",
            [column],
            unique=False,
        )

    op.create_table(
        "reward_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("contest_result_id", sa.Integer(), nullable=True),
        sa.Column("source_key", sa.String(length=150), nullable=False),
        sa.Column("reward_type", sa.String(length=50), nullable=False),
        sa.Column("category_key", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contest_result_id"], ["contest_results.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "source_key",
            "reward_type",
            name="uq_reward_grant_profile_source_type",
        ),
    )
    for column in ("contest_result_id", "profile_id", "reward_type", "source_key"):
        op.create_index(
            op.f(f"ix_reward_grants_{column}"),
            "reward_grants",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in ("source_key", "reward_type", "profile_id", "contest_result_id"):
        op.drop_index(op.f(f"ix_reward_grants_{column}"), table_name="reward_grants")
    op.drop_table("reward_grants")

    for column in (
        "subject_key",
        "profile_id",
        "division",
        "contest_week_id",
        "contest_id",
    ):
        op.drop_index(op.f(f"ix_contest_results_{column}"), table_name="contest_results")
    op.drop_table("contest_results")

    op.drop_index(op.f("ix_crown_progress_profile_id"), table_name="crown_progress")
    op.drop_index(op.f("ix_crown_progress_category_key"), table_name="crown_progress")
    op.drop_table("crown_progress")
    op.drop_index(op.f("ix_contest_weeks_week_start"), table_name="contest_weeks")
    op.drop_index(op.f("ix_contest_weeks_season_id"), table_name="contest_weeks")
    op.drop_table("contest_weeks")
    op.drop_index(op.f("ix_seasons_key"), table_name="seasons")
    op.drop_table("seasons")
    op.drop_index(op.f("ix_contests_key"), table_name="contests")
    op.drop_index(op.f("ix_contests_crown_category"), table_name="contests")
    op.drop_table("contests")
