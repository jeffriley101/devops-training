"""Add explicit persistent Team authority without promoting or copying rows.

The separately reviewed cutover is never run by this migration. Every existing
week retains legacy membership rules, including the currently open week.
"""
from alembic import op
import sqlalchemy as sa

revision = "p20team001"
down_revision = "f19arcade001"
branch_labels = None
depends_on = None

NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _season_metadata(table, *, nullable, ondelete):
    fk = next(f for f in sa.inspect(op.get_bind()).get_foreign_keys(table)
              if f["constrained_columns"] == ["season_id"])
    name = fk["name"] or f"fk_{table}_season_id_seasons"
    with op.batch_alter_table(table, naming_convention=NAMING) as batch:
        batch.drop_constraint(name, type_="foreignkey")
        batch.alter_column("season_id", existing_type=sa.Integer(), nullable=nullable)
        batch.create_foreign_key(name, "seasons", ["season_id"], ["id"], ondelete=ondelete)


def _partial(name, table, columns, predicate):
    op.create_index(name, table, columns, unique=True,
                    sqlite_where=sa.text(predicate), postgresql_where=sa.text(predicate))


def upgrade():
    for table, marker in (("teams", "is_operating"),
                          ("team_memberships", "is_persistent"),
                          ("team_join_requests", "is_persistent")):
        op.add_column(table, sa.Column(marker, sa.Boolean(), nullable=False, server_default=sa.false()))
        _season_metadata(table, nullable=True, ondelete="RESTRICT")

    _partial("uq_team_operating_family", "teams", ["family_id"], "is_operating = true")
    _partial("uq_team_operating_name", "teams", ["normalized_name"], "is_operating = true")
    _partial("uq_team_operating_emblem", "teams", ["emblem_key"], "is_operating = true")
    _partial("uq_team_operating_public_creator", "teams", ["creator_profile_id"],
             "is_operating = true AND visibility = 'public' AND creator_profile_id IS NOT NULL")
    op.drop_index("uq_team_membership_active_profile_season", table_name="team_memberships")
    _partial("uq_team_membership_active_profile_season", "team_memberships", ["profile_id", "season_id"],
             "ended_at IS NULL AND is_persistent = false")
    _partial("uq_team_membership_persistent_active_profile", "team_memberships", ["profile_id"],
             "is_persistent = true AND ended_at IS NULL")
    op.drop_index("uq_team_join_request_pending_profile_season", table_name="team_join_requests")
    _partial("uq_team_join_request_pending_profile_season", "team_join_requests", ["profile_id", "season_id"],
             "status = 'pending' AND is_persistent = false")
    _partial("uq_team_join_request_persistent_pending_profile", "team_join_requests", ["profile_id"],
             "status = 'pending' AND is_persistent = true")
    from app.team_authority_schema import install
    install(op.get_bind())

    with op.batch_alter_table("contest_weeks") as batch:
        batch.add_column(sa.Column("team_membership_rules_version", sa.String(40), nullable=False,
                                   server_default="legacy_seasonal_v1"))
        batch.create_check_constraint("ck_contest_week_team_membership_rules",
                                      "team_membership_rules_version IN ('legacy_seasonal_v1', 'persistent_v1')")
    with op.batch_alter_table("director_team_contests") as batch:
        batch.alter_column("season_id", existing_type=sa.Integer(), nullable=True)
    op.create_table("persistent_team_control",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
                    sa.Column("rules_from_week_start", sa.Date(), nullable=True),
                    sa.CheckConstraint("id = 1", name="ck_persistent_team_control_singleton"),
                    sa.CheckConstraint("(activated_at IS NULL AND rules_from_week_start IS NULL) OR "
                                       "(activated_at IS NOT NULL AND rules_from_week_start IS NOT NULL)",
                                       name="ck_persistent_team_control_activation"))
    op.execute(sa.text("INSERT INTO persistent_team_control (id) VALUES (1)"))
    op.create_table("team_membership_transitions",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("profile_id", sa.Integer(), nullable=False),
                    sa.Column("week_start", sa.Date(), nullable=False),
                    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("action", sa.String(10), nullable=False),
                    sa.Column("from_membership_id", sa.Integer(), nullable=True),
                    sa.Column("to_membership_id", sa.Integer(), nullable=True),
                    sa.ForeignKeyConstraint(["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"),
                    sa.ForeignKeyConstraint(["from_membership_id"], ["team_memberships.id"], ondelete="SET NULL"),
                    sa.ForeignKeyConstraint(["to_membership_id"], ["team_memberships.id"], ondelete="SET NULL"),
                    sa.CheckConstraint("action IN ('join', 'switch', 'leave')", name="ck_team_transition_action"))
    op.create_index("ix_team_transition_profile_week", "team_membership_transitions", ["profile_id", "week_start"])


def downgrade():
    bind = op.get_bind()
    # Once active, legacy writers cannot safely represent the authority. Refuse
    # destructive rollback, including NULL-origin rows created prospectively.
    for query in (
        "SELECT count(*) FROM teams WHERE is_operating = true OR season_id IS NULL",
        "SELECT count(*) FROM team_memberships WHERE is_persistent = true OR season_id IS NULL",
        "SELECT count(*) FROM team_join_requests WHERE is_persistent = true OR season_id IS NULL",
        "SELECT count(*) FROM persistent_team_control WHERE activated_at IS NOT NULL",
        "SELECT count(*) FROM team_membership_transitions",
        "SELECT count(*) FROM contest_weeks WHERE team_membership_rules_version <> 'legacy_seasonal_v1'",
        "SELECT count(*) FROM director_team_contests WHERE season_id IS NULL",
    ):
        if bind.scalar(sa.text(query)):
            raise RuntimeError("persistent_team_authority_in_use: downgrade refused")
    from app.team_authority_schema import uninstall
    uninstall(bind)
    op.drop_table("team_membership_transitions")
    op.drop_table("persistent_team_control")
    with op.batch_alter_table("director_team_contests") as batch:
        batch.alter_column("season_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("contest_weeks") as batch:
        batch.drop_constraint("ck_contest_week_team_membership_rules", type_="check")
        batch.drop_column("team_membership_rules_version")
    for name in ("uq_team_operating_family", "uq_team_operating_name", "uq_team_operating_emblem",
                 "uq_team_operating_public_creator"):
        op.drop_index(name, table_name="teams")
    op.drop_index("uq_team_membership_persistent_active_profile", table_name="team_memberships")
    op.drop_index("uq_team_membership_active_profile_season", table_name="team_memberships")
    _partial("uq_team_membership_active_profile_season", "team_memberships", ["profile_id", "season_id"], "ended_at IS NULL")
    op.drop_index("uq_team_join_request_persistent_pending_profile", table_name="team_join_requests")
    op.drop_index("uq_team_join_request_pending_profile_season", table_name="team_join_requests")
    _partial("uq_team_join_request_pending_profile_season", "team_join_requests", ["profile_id", "season_id"], "status = 'pending'")
    for table, marker in (("teams", "is_operating"), ("team_memberships", "is_persistent"),
                          ("team_join_requests", "is_persistent")):
        _season_metadata(table, nullable=False, ondelete="CASCADE")
        with op.batch_alter_table(table) as batch:
            batch.drop_column(marker)
