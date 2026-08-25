"""add Wheel of Woodchuck arcade score

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-08-24
"""

from alembic import op


revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint(
            "ck_arcade_high_score_game_key", type_="check"
        )
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner', 'wheel-of-woodchuck')",
        )


def downgrade() -> None:
    # The prior schema cannot represent Wheel scores. Remove only those rows
    # before restoring its narrower game-key constraint.
    op.execute(
        "DELETE FROM arcade_high_scores "
        "WHERE game_key = 'wheel-of-woodchuck'"
    )
    with op.batch_alter_table("arcade_high_scores") as batch:
        batch.drop_constraint(
            "ck_arcade_high_score_game_key", type_="check"
        )
        batch.create_check_constraint(
            "ck_arcade_high_score_game_key",
            "game_key IN ('blue', 'radio-tuner')",
        )
