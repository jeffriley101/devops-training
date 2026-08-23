"""preserve reward size preferences

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.alter_column(
            "placement_x",
            existing_type=sa.Float(),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "placement_y",
            existing_type=sa.Float(),
            existing_nullable=False,
            nullable=True,
        )
        batch.create_check_constraint(
            "ck_reward_inventory_placement_pair",
            "(placement_x IS NULL AND placement_y IS NULL) OR "
            "(placement_x IS NOT NULL AND placement_y IS NOT NULL)",
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM reward_inventory_placements "
        "WHERE placement_x IS NULL OR placement_y IS NULL"
    )
    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.drop_constraint(
            "ck_reward_inventory_placement_pair", type_="check"
        )
        batch.alter_column(
            "placement_y",
            existing_type=sa.Float(),
            existing_nullable=True,
            nullable=False,
        )
        batch.alter_column(
            "placement_x",
            existing_type=sa.Float(),
            existing_nullable=True,
            nullable=False,
        )
