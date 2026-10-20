"""Add issued weekly contracts and reserved instrument-plan storage; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "w25weekly003"
down_revision = "w25weekly002"
branch_labels = None
depends_on = None


def upgrade():
    # Frozen migration contract: no imports from changing runtime catalogs.
    op.create_table("weekly_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("focus", sa.String(64), nullable=False),
        sa.Column("issued_game_mode", sa.String(20), nullable=False),
        sa.Column("generator_version", sa.String(40), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recent_results_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recent_results", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("logging_days_target", sa.Integer(), nullable=False),
        sa.Column("practice_minutes_target", sa.Integer(), nullable=False),
        sa.Column("homework_minutes_target", sa.Integer(), nullable=False),
        sa.Column("thirds_target", sa.Integer(), nullable=False),
        sa.UniqueConstraint("profile_id", "week_start", name="uq_weekly_plan_profile_week"),
        sa.CheckConstraint("focus IN ('Build daily consistency', 'Improve tone and control', "
                           "'Learn songs faster', 'Strengthen improvisation')", name="ck_weekly_plan_focus"),
        sa.CheckConstraint("issued_game_mode IN ('Beginner', 'Challenge', 'Performance')", name="ck_weekly_plan_game_mode"),
        sa.CheckConstraint(
            "(issued_game_mode = 'Beginner' AND logging_days_target = 3 AND practice_minutes_target = 30 "
            "AND homework_minutes_target = 10 AND thirds_target = 3) OR "
            "(issued_game_mode = 'Challenge' AND logging_days_target = 4 AND practice_minutes_target = 60 "
            "AND homework_minutes_target = 15 AND thirds_target = 7) OR "
            "(issued_game_mode = 'Performance' AND logging_days_target = 5 AND practice_minutes_target = 90 "
            "AND homework_minutes_target = 20 AND thirds_target = 15)", name="ck_weekly_plan_mode_targets"),
        sa.CheckConstraint("length(trim(generator_version)) > 0", name="ck_weekly_plan_generator"),
        sa.CheckConstraint("recent_results_cutoff <= issued_at", name="ck_weekly_plan_cutoff"))
    op.create_table("weekly_instrument_plans",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("weekly_plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("slot_no", sa.Integer(), primary_key=True),
        sa.Column("instrument", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("homework_spec", sa.JSON(none_as_null=True), nullable=False),
        sa.CheckConstraint("slot_no IN (1, 2)", name="ck_weekly_instrument_slot"),
        sa.UniqueConstraint("plan_id", "instrument", name="uq_weekly_plan_instrument"),
        sa.CheckConstraint("length(trim(instrument)) > 0", name="ck_weekly_instrument_nonempty"))


def downgrade():
    # Preserve issued contracts/assignments, serialize the check with writers.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE weekly_plans, weekly_instrument_plans IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE weekly_plans SET id = id WHERE 0"))
    else:
        raise RuntimeError("weekly_plans_unsupported_database")
    for name in ("weekly_plans", "weekly_instrument_plans"):
        if bind.scalar(sa.text("SELECT count(*) FROM " + name)):
            raise RuntimeError("weekly_plans_in_use: downgrade refused")
    op.drop_table("weekly_instrument_plans")
    op.drop_table("weekly_plans")
