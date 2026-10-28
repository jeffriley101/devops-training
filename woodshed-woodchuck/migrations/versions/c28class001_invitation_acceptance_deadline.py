"""Record new deadline-policy acceptances without extending legacy permissions.

No backfill: even an unexpired legacy acceptance retains its original cutoff.
This remains safe if old application workers run between migration and rollout.
Only a fresh, timely acceptance in the new service records the marker. Expired
legacy relationships need a new invitation and explicit chart sharing choices.
"""
from alembic import op
import sqlalchemy as sa

revision = "c28class001"
down_revision = "c27class001"
branch_labels = None
depends_on = None

TABLE = "classroom_student_invitations"


def upgrade():
    # An additive nullable column leaves retained invitations and chart shares
    # byte-for-byte intact and gives old writers the safe NULL behavior.
    op.add_column(TABLE, sa.Column("deadline_accepted_at", sa.DateTime(timezone=True)))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text(f"UPDATE {TABLE} SET id = id WHERE 0"))
    else:
        raise RuntimeError("classroom_invitation_deadline_unsupported_database")
    # Do not erase policy evidence, including revoked invitation history.
    if bind.scalar(sa.text(f"SELECT count(*) FROM {TABLE} WHERE deadline_accepted_at IS NOT NULL")):
        raise RuntimeError("classroom_invitation_deadline_in_use: downgrade refused")
    # Native DROP COLUMN avoids rebuilding a table referenced by chart shares.
    op.drop_column(TABLE, "deadline_accepted_at")
