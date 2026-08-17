"""add repeatable crown awards and reward inventory placements

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa


revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crown_awards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("category_key", sa.String(length=100), nullable=False),
        sa.Column("source_key", sa.String(length=150), nullable=False),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["woodchuck_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "source_key",
            name="uq_crown_award_profile_source",
        ),
    )
    op.create_index("ix_crown_awards_profile_id", "crown_awards", ["profile_id"])
    op.create_index("ix_crown_awards_category_key", "crown_awards", ["category_key"])

    op.execute(
        """
        INSERT INTO crown_awards (
            profile_id, category_key, source_key, earned_at, created_at
        )
        SELECT
            profile_id,
            category_key,
            'legacy-crown-progress:' || CAST(id AS VARCHAR),
            crown_earned_at,
            crown_earned_at
        FROM crown_progress
        WHERE crown_earned_at IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE crown_progress
        SET
            qualifying_wins = CASE
                WHEN crown_earned_at IS NOT NULL THEN
                    CASE
                        WHEN qualifying_wins > 10 THEN (qualifying_wins - 10) % 10
                        ELSE 0
                    END
                WHEN qualifying_wins >= 10 THEN qualifying_wins % 10
                ELSE qualifying_wins
            END,
            crown_earned_at = NULL
        """
    )

    op.create_table(
        "reward_inventory_placements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("crown_award_id", sa.Integer(), nullable=False),
        sa.Column("placement_x", sa.Float(), nullable=False),
        sa.Column("placement_y", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "placement_x >= 0 AND placement_x <= 1",
            name="ck_reward_inventory_placement_x",
        ),
        sa.CheckConstraint(
            "placement_y >= 0 AND placement_y <= 1",
            name="ck_reward_inventory_placement_y",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["woodchuck_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["crown_award_id"],
            ["crown_awards.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "crown_award_id",
            name="uq_reward_inventory_placement_crown_award",
        ),
    )
    op.create_index(
        "ix_reward_inventory_placements_profile_id",
        "reward_inventory_placements",
        ["profile_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reward_inventory_placements_profile_id",
        table_name="reward_inventory_placements",
    )
    op.drop_table("reward_inventory_placements")

    op.execute(
        """
        UPDATE crown_progress
        SET
            qualifying_wins = qualifying_wins + 10,
            crown_earned_at = (
                SELECT MIN(crown_awards.earned_at)
                FROM crown_awards
                WHERE crown_awards.profile_id = crown_progress.profile_id
                  AND crown_awards.category_key = crown_progress.category_key
            )
        WHERE EXISTS (
            SELECT 1
            FROM crown_awards
            WHERE crown_awards.profile_id = crown_progress.profile_id
              AND crown_awards.category_key = crown_progress.category_key
        )
        """
    )
    op.drop_index("ix_crown_awards_category_key", table_name="crown_awards")
    op.drop_index("ix_crown_awards_profile_id", table_name="crown_awards")
    op.drop_table("crown_awards")
