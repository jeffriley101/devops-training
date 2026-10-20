"""Add nullable instrument preference and durable Wheel-play storage after Classroom.

No preference backfill, reward grants, runtime activation or historical rewrites.
Downgrade refuses to erase preferences or funded-play history.
"""
from alembic import op
import sqlalchemy as sa

revision = "w25weekly001"
down_revision = "c24class001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("woodchuck_profiles", sa.Column("first_choice_instrument", sa.String(50), nullable=True))
    op.create_table(
        "weekly_wheel_plays",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("funded_day", sa.Date(), nullable=False),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("daily_ordinal", sa.Integer(), nullable=False),
        sa.Column("outcomes", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("profile_id", "request_key", name="uq_weekly_wheel_request"),
        sa.UniqueConstraint("profile_id", "funded_day", "daily_ordinal", name="uq_weekly_wheel_day_slot"),
        sa.CheckConstraint("daily_ordinal BETWEEN 1 AND 9", name="ck_weekly_wheel_daily_ordinal"),
    )
    op.create_index("ix_weekly_wheel_plays_profile_id", "weekly_wheel_plays", ["profile_id"])


def downgrade():
    bind = op.get_bind()
    # Serialize the refusal check with writes before removing either storage field.
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE woodchuck_profiles, weekly_wheel_plays IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE woodchuck_profiles SET id = id WHERE 0"))
    else:
        raise RuntimeError("weekly_foundation_unsupported_database")
    if (bind.scalar(sa.text("SELECT count(*) FROM weekly_wheel_plays"))
            or bind.scalar(sa.text("SELECT count(*) FROM woodchuck_profiles WHERE first_choice_instrument IS NOT NULL"))):
        raise RuntimeError("weekly_foundation_in_use: downgrade refused")
    op.drop_table("weekly_wheel_plays")
    op.drop_column("woodchuck_profiles", "first_choice_instrument")
