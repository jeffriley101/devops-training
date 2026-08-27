"""add Thirds Arcade game

Revision ID: f6a7b8c9d0e1
Revises: e6f7a8b9c0d1
Create Date: 2026-08-27
"""

from alembic import op


revision = "f6a7b8c9d0e1"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint("ck_arcade_high_score_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck', "
            "'scale-keyboard', 'thirds')",
        )

    with op.batch_alter_table("arcade_play_sessions") as batch:
        batch.drop_constraint("ck_arcade_play_session_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_play_session_game_key",
            "game_key IN ('plunge-burrow', 'blue', 'radio-tuner', "
            "'wheel-of-woodchuck', 'scale-keyboard', 'thirds')",
        )


def downgrade() -> None:
    op.execute("DELETE FROM arcade_play_sessions WHERE game_key = 'thirds'")
    op.execute("DELETE FROM arcade_high_scores WHERE game_key = 'thirds'")

    with op.batch_alter_table("arcade_play_sessions") as batch:
        batch.drop_constraint("ck_arcade_play_session_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_play_session_game_key",
            "game_key IN ('plunge-burrow', 'blue', 'radio-tuner', "
            "'wheel-of-woodchuck', 'scale-keyboard')",
        )

    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint("ck_arcade_high_score_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck', "
            "'scale-keyboard')",
        )
