"""add director dashboard contests

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa


revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Public team creation keeps its one-team-per-season guard. Private,
    # director-led teams may share an owner so one director can manage several.
    with op.batch_alter_table("teams") as batch:
        batch.drop_constraint("uq_team_season_creator", type_="unique")
    op.create_index(
        "uq_team_public_season_creator",
        "teams",
        ["season_id", "creator_profile_id"],
        unique=True,
        sqlite_where=sa.text("visibility = 'public' AND creator_profile_id IS NOT NULL"),
        postgresql_where=sa.text("visibility = 'public' AND creator_profile_id IS NOT NULL"),
    )

    op.create_table(
        "director_team_contests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("owner_profile_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="scheduled", nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "metric IN ('total_minutes', 'average_minutes', 'team_practice_rating')",
            name="ck_director_team_contest_metric",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'open', 'finalized')",
            name="ck_director_team_contest_status",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_director_team_contest_window"),
        sa.CheckConstraint(
            "finalizes_at >= ends_at", name="ck_director_team_contest_finalization_window"
        ),
        sa.ForeignKeyConstraint(
            ["owner_profile_id"], ["woodchuck_profiles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_director_team_contests_season_id",
        "director_team_contests", ["season_id"], unique=False,
    )
    op.create_index(
        "ix_director_team_contests_owner_profile_id",
        "director_team_contests", ["owner_profile_id"], unique=False,
    )
    op.create_index(
        "ix_director_team_contests_metric",
        "director_team_contests", ["metric"], unique=False,
    )
    op.create_index(
        "ix_director_team_contests_starts_at",
        "director_team_contests", ["starts_at"], unique=False,
    )
    op.create_index(
        "ix_director_team_contests_ends_at",
        "director_team_contests", ["ends_at"], unique=False,
    )
    op.create_index(
        "ix_director_team_contests_finalizes_at",
        "director_team_contests", ["finalizes_at"], unique=False,
    )
    op.create_index(
        "ix_director_team_contests_status",
        "director_team_contests", ["status"], unique=False,
    )
    op.create_index(
        "ix_director_team_contest_owner_status",
        "director_team_contests", ["owner_profile_id", "status"], unique=False,
    )

    op.create_table(
        "director_team_contest_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contest_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contest_id"], ["director_team_contests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contest_id", "team_id", name="uq_director_contest_team"),
    )
    op.create_index(
        "ix_director_team_contest_entries_contest_id",
        "director_team_contest_entries", ["contest_id"], unique=False,
    )
    op.create_index(
        "ix_director_team_contest_entries_team_id",
        "director_team_contest_entries", ["team_id"], unique=False,
    )

    op.create_table(
        "director_team_contest_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contest_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("team_name_snapshot", sa.String(length=100), nullable=False),
        sa.Column("emblem_key_snapshot", sa.String(length=50), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("active_participant_count", sa.Integer(), nullable=False),
        sa.Column("eligible_roster_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rank > 0", name="ck_director_contest_result_rank"),
        sa.CheckConstraint("score >= 0", name="ck_director_contest_result_score"),
        sa.ForeignKeyConstraint(
            ["contest_id"], ["director_team_contests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contest_id", "team_id", name="uq_director_contest_result_team"
        ),
    )
    op.create_index(
        "ix_director_team_contest_results_contest_id",
        "director_team_contest_results", ["contest_id"], unique=False,
    )
    op.create_index(
        "ix_director_team_contest_results_team_id",
        "director_team_contest_results", ["team_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_director_team_contest_results_team_id",
        table_name="director_team_contest_results",
    )
    op.drop_index(
        "ix_director_team_contest_results_contest_id",
        table_name="director_team_contest_results",
    )
    op.drop_table("director_team_contest_results")
    op.drop_index(
        "ix_director_team_contest_entries_team_id",
        table_name="director_team_contest_entries",
    )
    op.drop_index(
        "ix_director_team_contest_entries_contest_id",
        table_name="director_team_contest_entries",
    )
    op.drop_table("director_team_contest_entries")
    op.drop_index(
        "ix_director_team_contest_owner_status",
        table_name="director_team_contests",
    )
    op.drop_index("ix_director_team_contests_status", table_name="director_team_contests")
    op.drop_index("ix_director_team_contests_finalizes_at", table_name="director_team_contests")
    op.drop_index("ix_director_team_contests_ends_at", table_name="director_team_contests")
    op.drop_index("ix_director_team_contests_starts_at", table_name="director_team_contests")
    op.drop_index("ix_director_team_contests_metric", table_name="director_team_contests")
    op.drop_index(
        "ix_director_team_contests_owner_profile_id",
        table_name="director_team_contests",
    )
    op.drop_index(
        "ix_director_team_contests_season_id",
        table_name="director_team_contests",
        if_exists=True,
    )
    op.drop_table("director_team_contests")

    op.drop_index("uq_team_public_season_creator", table_name="teams")
    with op.batch_alter_table("teams") as batch:
        batch.create_unique_constraint(
            "uq_team_season_creator", ["season_id", "creator_profile_id"]
        )
