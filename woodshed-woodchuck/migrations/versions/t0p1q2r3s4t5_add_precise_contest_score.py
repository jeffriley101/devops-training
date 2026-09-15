"""Preserve precise scores for newly finalized practice contests.

Revision ID: t0p1q2r3s4t5
Revises: s9n0o1p2q3r4
"""
from alembic import op
import sqlalchemy as sa

revision = "t0p1q2r3s4t5"
down_revision = "s9n0o1p2q3r4"
branch_labels = None
depends_on = None


def upgrade():
    # No score backfill: existing score/rank/reward snapshots remain authoritative.
    op.add_column("contest_results", sa.Column("precise_score", sa.Float(), nullable=True))
    op.add_column("contest_weeks", sa.Column("practice_scoring_mode", sa.String(30), nullable=True))
    # This migration introduces precision. Weeks already finalized before it
    # necessarily used the old rules, even if their result sets are incomplete.
    # Do not default future rows: an old finalizer running after this migration
    # cannot attest to its scoring mode and must leave an unknown (NULL) marker.
    op.execute("UPDATE contest_weeks SET practice_scoring_mode = 'legacy_minutes' "
               "WHERE status = 'finalized'")


def downgrade():
    with op.batch_alter_table("contest_weeks") as batch:
        batch.drop_column("practice_scoring_mode")
    with op.batch_alter_table("contest_results") as batch:
        batch.drop_column("precise_score")
