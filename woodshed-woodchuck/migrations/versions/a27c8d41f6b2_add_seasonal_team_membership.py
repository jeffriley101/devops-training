"""add seasonal team membership

Revision ID: a27c8d41f6b2
Revises: 61bb9ee21ca0
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa


revision = "a27c8d41f6b2"
down_revision = "61bb9ee21ca0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(30), nullable=False),
        sa.Column("normalized_name", sa.String(100), nullable=False),
        sa.Column("emblem_key", sa.String(50), nullable=False),
        sa.Column("creator_profile_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creator_profile_id"], ["woodchuck_profiles.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("season_id", "normalized_name", name="uq_team_season_name"),
        sa.UniqueConstraint("season_id", "emblem_key", name="uq_team_season_emblem"),
        sa.UniqueConstraint("season_id", "creator_profile_id", name="uq_team_season_creator"),
    )
    op.create_index("ix_teams_season_id", "teams", ["season_id"])
    op.create_index("ix_teams_normalized_name", "teams", ["normalized_name"])
    op.create_index("ix_teams_creator_profile_id", "teams", ["creator_profile_id"])

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("selected_week_start", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"),
    )
    for column in ("season_id", "team_id", "profile_id", "selected_week_start", "started_at", "ended_at"):
        op.create_index(f"ix_team_memberships_{column}", "team_memberships", [column])
    op.create_index("ix_team_membership_season_team", "team_memberships", ["season_id", "team_id"])
    op.create_index(
        "uq_team_membership_active_profile_season", "team_memberships",
        ["profile_id", "season_id"], unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "team_week_membership_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contest_week_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("membership_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["contest_week_id"], ["contest_weeks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["membership_id"], ["team_memberships.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("contest_week_id", "profile_id", name="uq_team_week_snapshot_profile"),
    )
    for column in ("contest_week_id", "profile_id", "team_id"):
        op.create_index(f"ix_team_week_membership_snapshots_{column}", "team_week_membership_snapshots", [column])
    op.create_index("ix_team_week_snapshot_week_team", "team_week_membership_snapshots", ["contest_week_id", "team_id"])

    with op.batch_alter_table("practice_charts") as batch:
        batch.add_column(sa.Column("include_team_contests", sa.Boolean(), server_default="1", nullable=False))
        batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_practice_charts_team_id", "teams", ["team_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_practice_charts_team_id", ["team_id"])

    # Historical awards cannot be attributed honestly; their new team_id remains null.
    with op.batch_alter_table("camp_point_awards") as batch:
        batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_camp_point_awards_team_id", "teams", ["team_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_camp_point_awards_team_id", ["team_id"])

    with op.batch_alter_table("contest_results") as batch:
        batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("active_member_count", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_contest_results_team_id", "teams", ["team_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_contest_results_team_id", ["team_id"])
    with op.batch_alter_table("contests") as batch:
        batch.drop_constraint("ck_contest_subject_type", type_="check")
        batch.create_check_constraint("ck_contest_subject_type", "subject_type IN ('student', 'instrument', 'team')")
    with op.batch_alter_table("contest_results") as batch:
        batch.drop_constraint("ck_contest_result_subject_type", type_="check")
        batch.create_check_constraint("ck_contest_result_subject_type", "subject_type IN ('student', 'instrument', 'team')")


def downgrade() -> None:
    with op.batch_alter_table("contest_results") as batch:
        batch.drop_constraint("ck_contest_result_subject_type", type_="check")
        batch.create_check_constraint("ck_contest_result_subject_type", "subject_type IN ('student', 'instrument')")
    with op.batch_alter_table("contests") as batch:
        batch.drop_constraint("ck_contest_subject_type", type_="check")
        batch.create_check_constraint("ck_contest_subject_type", "subject_type IN ('student', 'instrument')")
    with op.batch_alter_table("contest_results") as batch:
        batch.drop_index("ix_contest_results_team_id")
        batch.drop_constraint("fk_contest_results_team_id", type_="foreignkey")
        batch.drop_column("active_member_count")
        batch.drop_column("team_id")
    with op.batch_alter_table("camp_point_awards") as batch:
        batch.drop_index("ix_camp_point_awards_team_id")
        batch.drop_constraint("fk_camp_point_awards_team_id", type_="foreignkey")
        batch.drop_column("team_id")
    with op.batch_alter_table("practice_charts") as batch:
        batch.drop_index("ix_practice_charts_team_id")
        batch.drop_constraint("fk_practice_charts_team_id", type_="foreignkey")
        batch.drop_column("team_id")
        batch.drop_column("include_team_contests")
    op.drop_table("team_week_membership_snapshots")
    op.drop_table("team_memberships")
    op.drop_table("teams")
