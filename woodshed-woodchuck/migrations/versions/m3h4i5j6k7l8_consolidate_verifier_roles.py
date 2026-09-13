"""Consolidate student verifier relationships; no schema or history changes.

Revision ID: m3h4i5j6k7l8
Revises: l2g3h4i5j6k7
"""
from alembic import op

revision = "m3h4i5j6k7l8"
down_revision = "l2g3h4i5j6k7"
branch_labels = None
depends_on = None


def upgrade():
    # Raw SQL deliberately preserves updated_at and every other stored field.
    # Organization membership roles are a separate domain, not student roles.
    for table in ("trusted_verifier_invitations", "student_verifier_connections"):
        op.execute(f"UPDATE {table} SET role = 'mentor' WHERE role IN "
                   "('guardian', 'private_teacher', 'coach', 'other_trusted_adult')")


def downgrade():
    # The original four meanings cannot be recovered. Use the broadest legacy
    # representation for ALL mentors, including mentors created after upgrade.
    for table in ("trusted_verifier_invitations", "student_verifier_connections"):
        op.execute(f"UPDATE {table} SET role = 'other_trusted_adult' WHERE role = 'mentor'")
