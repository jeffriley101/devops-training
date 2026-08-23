"""add inventory sizes and placeable permanent rewards

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("owned_item_copies") as batch:
        batch.add_column(
            sa.Column(
                "placement_size",
                sa.String(length=10),
                server_default="medium",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "ck_owned_item_copy_placement_size",
            "placement_size IN ('small', 'medium', 'large')",
        )

    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.add_column(sa.Column("reward_grant_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reward_ordinal", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "placement_size",
                sa.String(length=10),
                server_default="medium",
                nullable=False,
            )
        )
        batch.alter_column(
            "crown_award_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch.create_foreign_key(
            "fk_reward_inventory_placement_reward_grant",
            "reward_grants",
            ["reward_grant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_reward_inventory_placement_grant_ordinal",
            ["reward_grant_id", "reward_ordinal"],
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_source",
            "(crown_award_id IS NOT NULL AND reward_grant_id IS NULL "
            "AND reward_ordinal IS NULL) OR "
            "(crown_award_id IS NULL AND reward_grant_id IS NOT NULL "
            "AND reward_ordinal IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_ordinal",
            "reward_ordinal IS NULL OR reward_ordinal > 0",
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_size",
            "placement_size IN ('small', 'medium', 'large')",
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM reward_inventory_placements "
        "WHERE reward_grant_id IS NOT NULL"
    )
    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.drop_constraint(
            "ck_reward_inventory_placement_size", type_="check"
        )
        batch.drop_constraint(
            "ck_reward_inventory_placement_ordinal", type_="check"
        )
        batch.drop_constraint(
            "ck_reward_inventory_placement_source", type_="check"
        )
        batch.drop_constraint(
            "uq_reward_inventory_placement_grant_ordinal", type_="unique"
        )
        batch.drop_constraint(
            "fk_reward_inventory_placement_reward_grant", type_="foreignkey"
        )
        batch.alter_column(
            "crown_award_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch.drop_column("placement_size")
        batch.drop_column("reward_ordinal")
        batch.drop_column("reward_grant_id")

    with op.batch_alter_table("owned_item_copies") as batch:
        batch.drop_constraint(
            "ck_owned_item_copy_placement_size", type_="check"
        )
        batch.drop_column("placement_size")
