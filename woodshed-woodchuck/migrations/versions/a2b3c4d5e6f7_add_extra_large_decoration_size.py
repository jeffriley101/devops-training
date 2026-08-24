"""add extra large decoration size

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Small is no longer a user-facing size. Preserve every placement and
    # preference while moving it to the new minimum, Medium.
    op.execute(
        "UPDATE owned_item_copies SET placement_size = 'medium' "
        "WHERE placement_size = 'small'"
    )
    op.execute(
        "UPDATE reward_inventory_placements SET placement_size = 'medium' "
        "WHERE placement_size = 'small'"
    )
    with op.batch_alter_table("owned_item_copies") as batch:
        batch.drop_constraint(
            "ck_owned_item_copy_placement_size", type_="check"
        )
        batch.create_check_constraint(
            "ck_owned_item_copy_placement_size",
            "placement_size IN ('medium', 'large', 'xlarge')",
        )
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="medium",
        )
    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.drop_constraint(
            "ck_reward_inventory_placement_size", type_="check"
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_size",
            "placement_size IN ('medium', 'large', 'xlarge')",
        )
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="medium",
        )


def downgrade() -> None:
    # The prior schema has no XL value. Keep ownership and placement intact by
    # reducing only XL preferences to the largest supported prior size.
    op.execute(
        "UPDATE owned_item_copies SET placement_size = 'large' "
        "WHERE placement_size = 'xlarge'"
    )
    op.execute(
        "UPDATE reward_inventory_placements SET placement_size = 'large' "
        "WHERE placement_size = 'xlarge'"
    )
    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.drop_constraint(
            "ck_reward_inventory_placement_size", type_="check"
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_size",
            "placement_size IN ('small', 'medium', 'large')",
        )
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="small",
        )
    with op.batch_alter_table("owned_item_copies") as batch:
        batch.drop_constraint(
            "ck_owned_item_copy_placement_size", type_="check"
        )
        batch.create_check_constraint(
            "ck_owned_item_copy_placement_size",
            "placement_size IN ('small', 'medium', 'large')",
        )
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="small",
        )
