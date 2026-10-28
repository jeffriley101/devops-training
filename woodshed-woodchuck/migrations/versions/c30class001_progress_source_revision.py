"""Bind fresh student acceptance to retained, scoped source history.

No backfill: existing acceptance evidence is retained but cannot prove that
its sources never changed. A fresh eligible student opt-in is required.
"""
from alembic import op
import sqlalchemy as sa

revision = "c30class001"
down_revision = "c29class001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("classroom_progress_authorizations", sa.Column("source_revision", sa.String(64), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE classroom_progress_authorizations IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE classroom_progress_authorizations SET id = id WHERE 0"))
    else:
        raise RuntimeError("classroom_progress_unsupported_database")
    if bind.scalar(sa.text("SELECT count(*) FROM classroom_progress_authorizations")):
        raise RuntimeError("classroom_progress_evidence_in_use: downgrade refused")
    # Native DROP COLUMN preserves the referenced parent table and its children.
    op.drop_column("classroom_progress_authorizations", "source_revision")
