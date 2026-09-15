"""Add isolated daily feature entry observations.

Revision ID: u1q2r3s4t5u6
Revises: t0p1q2r3s4t5
"""
from alembic import op
import sqlalchemy as sa

revision = "u1q2r3s4t5u6"
down_revision = "t0p1q2r3s4t5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"),
        sa.CheckConstraint("event_type IN ('arcade_entered', 'pristine_entered')",
                           name="ck_analytics_event_type"),
        sa.UniqueConstraint("profile_id", "event_type", "activity_date",
                            name="uq_analytics_event_profile_type_day"),
    )
    op.create_index("ix_analytics_events_occurred_at", "analytics_events", ["occurred_at"])


def downgrade():
    op.drop_index("ix_analytics_events_occurred_at", table_name="analytics_events")
    op.drop_table("analytics_events")
