"""add camp point awards

Revision ID: 0c6d66da9ea3
Revises: 4d9fb7211ac8
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0c6d66da9ea3"
down_revision: Union[str, Sequence[str], None] = "4d9fb7211ac8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "camp_point_awards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("activity_type", sa.String(length=50), nullable=False),
        sa.Column("points_awarded", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duplicate_key", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("points_awarded > 0", name="ck_camp_point_award_positive"),
        sa.ForeignKeyConstraint(["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "duplicate_key", name="uq_camp_point_award_profile_key"),
    )
    op.create_index("ix_camp_point_awards_profile_id", "camp_point_awards", ["profile_id"])
    op.create_index("ix_camp_point_awards_activity_type", "camp_point_awards", ["activity_type"])
    op.create_index("ix_camp_point_awards_occurred_at", "camp_point_awards", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_camp_point_awards_occurred_at", table_name="camp_point_awards")
    op.drop_index("ix_camp_point_awards_activity_type", table_name="camp_point_awards")
    op.drop_index("ix_camp_point_awards_profile_id", table_name="camp_point_awards")
    op.drop_table("camp_point_awards")
