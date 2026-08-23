"""add arcade high scores

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "arcade_high_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("game_key", sa.String(length=30), nullable=False),
        sa.Column("best_score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "game_key IN ('blue', 'radio-tuner')",
            name="ck_arcade_high_score_game_key",
        ),
        sa.CheckConstraint(
            "best_score >= 0",
            name="ck_arcade_high_score_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["woodchuck_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "game_key",
            name="uq_arcade_high_score_profile_game",
        ),
    )
    op.create_index(
        "ix_arcade_high_scores_profile_id",
        "arcade_high_scores",
        ["profile_id"],
    )
    op.create_index(
        "ix_arcade_high_scores_game_score",
        "arcade_high_scores",
        ["game_key", "best_score"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_arcade_high_scores_game_score",
        table_name="arcade_high_scores",
    )
    op.drop_index(
        "ix_arcade_high_scores_profile_id",
        table_name="arcade_high_scores",
    )
    op.drop_table("arcade_high_scores")
