"""Retire authenticated browser sessions without anonymous tracking.

Revision ID: v2r3s4t5u6v7
Revises: u1q2r3s4t5u6
"""

from alembic import op
import sqlalchemy as sa

revision = "v2r3s4t5u6v7"
down_revision = "u1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "revoked_browser_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_revoked_browser_sessions_expires_at",
        "revoked_browser_sessions",
        ["expires_at"],
    )


def downgrade():
    op.drop_index(
        "ix_revoked_browser_sessions_expires_at", table_name="revoked_browser_sessions"
    )
    op.drop_table("revoked_browser_sessions")
