"""Unify student verifier relationships while retaining Band Directors.

Revision ID: n4i5j6k7l8m9
Revises: m3h4i5j6k7l8
"""
from alembic import op

revision = "n4i5j6k7l8m9"
down_revision = "m3h4i5j6k7l8"
branch_labels = None
depends_on = None


def upgrade():
    # Data only: keep IDs, status, tokens, timestamps and verification history.
    # Include older values defensively; organization roles are a separate domain.
    for table in ("trusted_verifier_invitations", "student_verifier_connections"):
        op.execute(f"UPDATE {table} SET role = 'verifier' WHERE role IN "
                   "('parent', 'mentor', 'guardian', 'private_teacher', 'coach', 'other_trusted_adult')")


def downgrade():
    # Original Parent/Mentor distinctions cannot be recovered. Mentor is the
    # least-privileged supported representation in the previous application.
    for table in ("trusted_verifier_invitations", "student_verifier_connections"):
        op.execute(f"UPDATE {table} SET role = 'mentor' WHERE role = 'verifier'")
