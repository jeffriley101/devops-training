"""add temporary traveling cup placement preferences

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = "k1f2a3b4c5d6"
down_revision = "j0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traveling_cup_placements",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("entitlement_key", sa.String(length=40), nullable=False),
        sa.Column("placement_x", sa.Float(), nullable=True),
        sa.Column("placement_y", sa.Float(), nullable=True),
        sa.Column(
            "placement_size",
            sa.String(length=10),
            server_default="xlarge",
            nullable=False,
        ),
        sa.CheckConstraint(
            "entitlement_key IN ('punxsutawney-cup', 'coterie-cup')",
            name="ck_traveling_cup_placement_key",
        ),
        sa.CheckConstraint(
            "(placement_x IS NULL AND placement_y IS NULL) OR "
            "(placement_x IS NOT NULL AND placement_y IS NOT NULL)",
            name="ck_traveling_cup_placement_pair",
        ),
        sa.CheckConstraint(
            "placement_x >= 0 AND placement_x <= 1",
            name="ck_traveling_cup_placement_x",
        ),
        sa.CheckConstraint(
            "placement_y >= 0 AND placement_y <= 1",
            name="ck_traveling_cup_placement_y",
        ),
        sa.CheckConstraint(
            "placement_size IN ('medium', 'large', 'xlarge')",
            name="ck_traveling_cup_placement_size",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["woodchuck_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("profile_id", "entitlement_key"),
    )


def downgrade() -> None:
    op.drop_table("traveling_cup_placements")
