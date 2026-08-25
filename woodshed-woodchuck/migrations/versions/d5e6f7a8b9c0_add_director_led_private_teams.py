"""add director-led private teams

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("teams") as batch:
        batch.add_column(sa.Column(
            "visibility", sa.String(length=20), server_default="public",
            nullable=False,
        ))
        batch.add_column(sa.Column(
            "director_led", sa.Boolean(), server_default=sa.false(),
            nullable=False,
        ))
        batch.add_column(sa.Column("join_code", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "ck_team_visibility", "visibility IN ('public', 'private')"
        )
        batch.create_check_constraint(
            "ck_team_director_led_private",
            "(director_led = false AND join_code IS NULL) OR "
            "(director_led = true AND visibility = 'private' AND join_code IS NOT NULL)",
        )
        batch.create_unique_constraint("uq_team_join_code", ["join_code"])
        batch.create_index("ix_teams_visibility", ["visibility"])
        batch.create_index("ix_teams_director_led", ["director_led"])
        batch.create_index("ix_teams_join_code", ["join_code"])

    op.create_table(
        "profile_capabilities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(length=40), nullable=False),
        sa.Column(
            "granted_by", sa.String(length=40), server_default="contest-admin",
            nullable=False,
        ),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capability IN ('band_director')",
            name="ck_profile_capability_value",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id", "capability", name="uq_profile_capability"
        ),
    )
    op.create_index(
        "ix_profile_capabilities_profile_id", "profile_capabilities",
        ["profile_id"], unique=False,
    )
    op.create_index(
        "ix_profile_capabilities_capability", "profile_capabilities",
        ["capability"], unique=False,
    )

    op.create_table(
        "team_join_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="pending",
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_profile_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_team_join_request_status",
        ),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_profile_id"], ["woodchuck_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_join_requests_season_id", "team_join_requests", ["season_id"]
    )
    op.create_index(
        "ix_team_join_requests_team_id", "team_join_requests", ["team_id"]
    )
    op.create_index(
        "ix_team_join_requests_profile_id", "team_join_requests", ["profile_id"]
    )
    op.create_index(
        "ix_team_join_requests_status", "team_join_requests", ["status"]
    )
    op.create_index(
        "ix_team_join_request_team_status", "team_join_requests",
        ["team_id", "status"]
    )
    op.create_index(
        "uq_team_join_request_pending_profile_season", "team_join_requests",
        ["profile_id", "season_id"], unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_team_join_request_pending_profile_season",
        table_name="team_join_requests",
    )
    op.drop_index("ix_team_join_request_team_status", table_name="team_join_requests")
    op.drop_index("ix_team_join_requests_status", table_name="team_join_requests")
    op.drop_index("ix_team_join_requests_profile_id", table_name="team_join_requests")
    op.drop_index("ix_team_join_requests_team_id", table_name="team_join_requests")
    op.drop_index("ix_team_join_requests_season_id", table_name="team_join_requests")
    op.drop_table("team_join_requests")

    op.drop_index("ix_profile_capabilities_capability", table_name="profile_capabilities")
    op.drop_index("ix_profile_capabilities_profile_id", table_name="profile_capabilities")
    op.drop_table("profile_capabilities")

    with op.batch_alter_table("teams") as batch:
        batch.drop_index("ix_teams_join_code")
        batch.drop_index("ix_teams_director_led")
        batch.drop_index("ix_teams_visibility")
        batch.drop_constraint("uq_team_join_code", type_="unique")
        batch.drop_constraint("ck_team_director_led_private", type_="check")
        batch.drop_constraint("ck_team_visibility", type_="check")
        batch.drop_column("join_code")
        batch.drop_column("director_led")
        batch.drop_column("visibility")
