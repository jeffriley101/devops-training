"""add Scale Keyboard scores and Arcade play sessions

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint(
            "ck_arcade_high_score_game_key", type_="check"
        )
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck', "
            "'scale-keyboard')",
        )

    op.create_table(
        "arcade_play_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("game_key", sa.String(length=30), nullable=False),
        sa.Column("play_token", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "entry_cost", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_score", sa.Integer(), nullable=True),
        sa.Column("payout", sa.Integer(), nullable=True),
        sa.Column(
            "reward_granted_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.CheckConstraint(
            "game_key IN ('plunge-burrow', 'blue', 'radio-tuner', "
            "'wheel-of-woodchuck', 'scale-keyboard')",
            name="ck_arcade_play_session_game_key",
        ),
        sa.CheckConstraint(
            "entry_cost = 1", name="ck_arcade_play_session_entry_cost"
        ),
        sa.CheckConstraint(
            "submitted_score IS NULL OR submitted_score >= 0",
            name="ck_arcade_play_session_score_nonnegative",
        ),
        sa.CheckConstraint(
            "payout IS NULL OR payout IN (0, 1, 2, 3, 5)",
            name="ck_arcade_play_session_payout",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "play_token", name="uq_arcade_play_session_token"
        ),
    )
    op.create_index(
        "ix_arcade_play_sessions_profile_id",
        "arcade_play_sessions",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        "ix_arcade_play_sessions_profile_game_completed",
        "arcade_play_sessions",
        ["profile_id", "game_key", "completed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_arcade_play_sessions_profile_game_completed",
        table_name="arcade_play_sessions",
    )
    op.drop_index(
        "ix_arcade_play_sessions_profile_id",
        table_name="arcade_play_sessions",
    )
    op.drop_table("arcade_play_sessions")

    # The prior schema cannot represent Scale Keyboard scores.
    op.execute(
        "DELETE FROM arcade_high_scores WHERE game_key = 'scale-keyboard'"
    )
    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint(
            "ck_arcade_high_score_game_key", type_="check"
        )
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck')",
        )
