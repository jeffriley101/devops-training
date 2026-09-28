"""Record finalizer rule provenance for future contest weeks.

Revision ID: d17contest001
Revises: d16team001

Existing finalized weeks remain unknown. Neither their date nor surviving
result rows prove which practice, activity, and team rules produced them.
"""
from alembic import op
import sqlalchemy as sa

revision = "d17contest001"
down_revision = "d16team001"
branch_labels = depends_on = None


def upgrade():
    # No default or backfill: old writers and old history cannot attest to the
    # current finalizer's semantics merely because this column now exists.
    op.add_column("contest_weeks", sa.Column("finalizer_rules_version", sa.String(80), nullable=True))


def downgrade():
    with op.batch_alter_table("contest_weeks") as batch:
        batch.drop_column("finalizer_rules_version")
