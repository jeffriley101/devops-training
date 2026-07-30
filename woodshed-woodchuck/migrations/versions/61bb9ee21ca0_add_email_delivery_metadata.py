"""add email delivery metadata

Revision ID: 61bb9ee21ca0
Revises: f4c7b19a2e60
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "61bb9ee21ca0"
down_revision = "f4c7b19a2e60"
branch_labels = None
depends_on = None


def _add(table: str) -> None:
    op.add_column(table, sa.Column("last_email_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("last_email_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("email_attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column(table, sa.Column("last_email_error_code", sa.String(length=40), nullable=True))


def upgrade() -> None:
    _add("trusted_verifier_invitations")
    _add("practice_chart_verifications")


def downgrade() -> None:
    for table in ("practice_chart_verifications", "trusted_verifier_invitations"):
        op.drop_column(table, "last_email_error_code")
        op.drop_column(table, "email_attempt_count")
        op.drop_column(table, "last_email_sent_at")
        op.drop_column(table, "last_email_attempt_at")
