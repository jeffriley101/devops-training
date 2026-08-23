"""remap legacy decoration sizes

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The first sizing release used "medium" for both the schema default and
    # every pre-sizing placement. Remap that legacy/default value before
    # making Medium intentionally much larger.
    op.execute(
        "UPDATE owned_item_copies SET placement_size = 'small' "
        "WHERE placement_size = 'medium'"
    )
    op.execute(
        "UPDATE reward_inventory_placements SET placement_size = 'small' "
        "WHERE placement_size = 'medium'"
    )
    with op.batch_alter_table("owned_item_copies") as batch:
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="small",
        )
    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="small",
        )


def downgrade() -> None:
    # Keep stored Small choices intact so a rollback cannot enlarge existing
    # decorations. Only restore the earlier default for newly inserted rows.
    with op.batch_alter_table("reward_inventory_placements") as batch:
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="medium",
        )
    with op.batch_alter_table("owned_item_copies") as batch:
        batch.alter_column(
            "placement_size",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="medium",
        )
