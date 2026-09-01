"""add Dressed to the Nines Arcade game

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-08-31
"""

from alembic import op


revision = "h8c9d0e1f2a3"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint("ck_arcade_high_score_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck', "
            "'scale-keyboard', 'thirds', 'dressed-to-the-nines')",
        )

    with op.batch_alter_table("arcade_play_sessions") as batch:
        batch.drop_constraint("ck_arcade_play_session_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_play_session_game_key",
            "game_key IN ('plunge-burrow', 'blue', 'radio-tuner', "
            "'wheel-of-woodchuck', 'scale-keyboard', 'thirds', "
            "'dressed-to-the-nines')",
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM arcade_play_sessions "
        "WHERE game_key = 'dressed-to-the-nines'"
    )
    op.execute(
        "DELETE FROM arcade_high_scores "
        "WHERE game_key = 'dressed-to-the-nines'"
    )

    with op.batch_alter_table("arcade_play_sessions") as batch:
        batch.drop_constraint("ck_arcade_play_session_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_play_session_game_key",
            "game_key IN ('plunge-burrow', 'blue', 'radio-tuner', "
            "'wheel-of-woodchuck', 'scale-keyboard', 'thirds')",
        )

    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint("ck_arcade_high_score_game_key", type_="check")
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck', "
            "'scale-keyboard', 'thirds')",
        )
