"""add plunge point awards

Revision ID: d3e4f5a6b7c8
Revises: c91f2d3e4a50
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e4f5a6b7c8"
down_revision = "c91f2d3e4a50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plunge_point_awards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("points_scored", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('dandelion', 'carrot', 'instrument', 'band_complete')",
            name="ck_plunge_point_award_event_type",
        ),
        sa.CheckConstraint(
            "points_scored > 0",
            name="ck_plunge_point_award_points_positive",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["woodchuck_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "event_key",
            name="uq_plunge_point_award_profile_event",
        ),
    )
    op.create_index(
        "ix_plunge_point_awards_profile_id",
        "plunge_point_awards",
        ["profile_id"],
    )
    op.create_index(
        "ix_plunge_point_awards_occurred_at",
        "plunge_point_awards",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_plunge_point_awards_occurred_at",
        table_name="plunge_point_awards",
    )
    op.drop_index(
        "ix_plunge_point_awards_profile_id",
        table_name="plunge_point_awards",
    )
    op.drop_table("plunge_point_awards")
