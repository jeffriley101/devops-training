"""add owned item copies

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owned_item_copies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(length=50), nullable=False),
        sa.Column("acquisition_source", sa.String(length=20), nullable=False),
        sa.Column("acquisition_key", sa.String(length=100), nullable=True),
        sa.Column("purchase_price", sa.Integer(), nullable=True),
        sa.Column("placement_x", sa.Float(), nullable=True),
        sa.Column("placement_y", sa.Float(), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "acquisition_source IN ('store', 'mum')",
            name="ck_owned_item_copy_source",
        ),
        sa.CheckConstraint(
            "(acquisition_source = 'store' AND purchase_price > 0) OR "
            "(acquisition_source = 'mum' AND purchase_price IS NULL)",
            name="ck_owned_item_copy_price_source",
        ),
        sa.CheckConstraint(
            "(placement_x IS NULL AND placement_y IS NULL) OR "
            "(placement_x IS NOT NULL AND placement_y IS NOT NULL)",
            name="ck_owned_item_copy_placement_pair",
        ),
        sa.CheckConstraint(
            "placement_x IS NULL OR (placement_x >= 0 AND placement_x <= 1)",
            name="ck_owned_item_copy_placement_x",
        ),
        sa.CheckConstraint(
            "placement_y IS NULL OR (placement_y >= 0 AND placement_y <= 1)",
            name="ck_owned_item_copy_placement_y",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["woodchuck_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "acquisition_key",
            name="uq_owned_item_copy_profile_acquisition",
        ),
    )
    op.create_index(
        "ix_owned_item_copies_profile_id",
        "owned_item_copies",
        ["profile_id"],
    )
    op.create_index(
        "ix_owned_item_copies_item_key",
        "owned_item_copies",
        ["item_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_owned_item_copies_item_key",
        table_name="owned_item_copies",
    )
    op.drop_index(
        "ix_owned_item_copies_profile_id",
        table_name="owned_item_copies",
    )
    op.drop_table("owned_item_copies")
