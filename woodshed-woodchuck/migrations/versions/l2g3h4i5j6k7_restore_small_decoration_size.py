"""restore small decoration size

Revision ID: l2g3h4i5j6k7
Revises: k1f2a3b4c5d6
Create Date: 2026-09-11
"""

from alembic import op


revision = "l2g3h4i5j6k7"
down_revision = "k1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("owned_item_copies") as batch:
        batch.drop_constraint(
            "ck_owned_item_copy_placement_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_owned_item_copy_placement_size",
            "placement_size IN ('small', 'medium', 'large', 'xlarge')",
        )

    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.drop_constraint(
            "ck_reward_inventory_placement_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_size",
            "placement_size IN ('small', 'medium', 'large', 'xlarge')",
        )

    with op.batch_alter_table("traveling_cup_placements") as batch:
        batch.drop_constraint(
            "ck_traveling_cup_placement_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_traveling_cup_placement_size",
            "placement_size IN ('small', 'medium', 'large', 'xlarge')",
        )


def downgrade() -> None:
    # A downgrade cannot retain rows using "small" because the older schema
    # does not permit that value. Convert only those rows for rollback safety.
    op.execute(
        "UPDATE owned_item_copies "
        "SET placement_size = 'medium' "
        "WHERE placement_size = 'small'"
    )
    op.execute(
        "UPDATE reward_inventory_placements "
        "SET placement_size = 'medium' "
        "WHERE placement_size = 'small'"
    )
    op.execute(
        "UPDATE traveling_cup_placements "
        "SET placement_size = 'medium' "
        "WHERE placement_size = 'small'"
    )

    with op.batch_alter_table("owned_item_copies") as batch:
        batch.drop_constraint(
            "ck_owned_item_copy_placement_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_owned_item_copy_placement_size",
            "placement_size IN ('medium', 'large', 'xlarge')",
        )

    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.drop_constraint(
            "ck_reward_inventory_placement_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_size",
            "placement_size IN ('medium', 'large', 'xlarge')",
        )

    with op.batch_alter_table("traveling_cup_placements") as batch:
        batch.drop_constraint(
            "ck_traveling_cup_placement_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_traveling_cup_placement_size",
            "placement_size IN ('medium', 'large', 'xlarge')",
        )
