"""add History Mystery Arcade game and daily-play guard

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = "j0e1f2a3b4c5"
down_revision = "i9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint("ck_arcade_high_score_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck', "
            "'scale-keyboard', 'thirds', 'dressed-to-the-nines', "
            "'interval-basic-training', 'history-mystery')",
        )

    with op.batch_alter_table("arcade_play_sessions") as batch:
        batch.add_column(sa.Column("daily_play_date", sa.Date(), nullable=True))
        batch.drop_constraint("ck_arcade_play_session_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_play_session_game_key",
            "game_key IN ('plunge-burrow', 'blue', 'radio-tuner', "
            "'wheel-of-woodchuck', 'scale-keyboard', 'thirds', "
            "'dressed-to-the-nines', 'interval-basic-training', "
            "'history-mystery')",
        )
        batch.create_check_constraint(
            "ck_arcade_play_session_daily_date_scope",
            "(game_key = 'history-mystery' AND daily_play_date IS NOT NULL) OR "
            "(game_key <> 'history-mystery' AND daily_play_date IS NULL)",
        )
        batch.create_unique_constraint(
            "uq_arcade_play_session_profile_game_daily_date",
            ["profile_id", "game_key", "daily_play_date"],
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM arcade_play_sessions WHERE game_key = 'history-mystery'"
    )
    op.execute(
        "DELETE FROM arcade_high_scores WHERE game_key = 'history-mystery'"
    )

    with op.batch_alter_table("arcade_play_sessions") as batch:
        batch.drop_constraint(
            "uq_arcade_play_session_profile_game_daily_date", type_="unique"
        )
        batch.drop_constraint(
            "ck_arcade_play_session_daily_date_scope", type_="check"
        )
        batch.drop_constraint("ck_arcade_play_session_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_play_session_game_key",
            "game_key IN ('plunge-burrow', 'blue', 'radio-tuner', "
            "'wheel-of-woodchuck', 'scale-keyboard', 'thirds', "
            "'dressed-to-the-nines', 'interval-basic-training')",
        )
        batch.drop_column("daily_play_date")

    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint("ck_arcade_high_score_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck', "
            "'scale-keyboard', 'thirds', 'dressed-to-the-nines', "
            "'interval-basic-training')",
        )

