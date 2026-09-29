"""C001 entry attribution and additive abuse-control storage.

Existing enrollments/claims keep NULL: their source is unknown.
"""
from alembic import op
import sqlalchemy as sa

revision = "e18tester001"
down_revision = "d17contest001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tester_enrollments", sa.Column("source", sa.String(40), nullable=True))
    op.add_column("child_pending_consents", sa.Column("cohort_source", sa.String(40), nullable=True))


    op.create_table("c001_activation_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("emergency_ceiling", sa.Integer(), nullable=False, server_default="1000"),
        sa.CheckConstraint("id = 1", name="ck_c001_control_singleton"),
        sa.CheckConstraint("emergency_ceiling > 0", name="ck_c001_emergency_ceiling"))
    op.execute(sa.text("INSERT INTO c001_activation_control (id, enabled, emergency_ceiling) VALUES (1, true, 1000)"))
    op.create_table("c001_abuse_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("network_key", sa.String(64), nullable=True),
        sa.Column("browser_key", sa.String(64), nullable=True),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_c001_abuse_network_kind_time", "c001_abuse_events", ["network_key", "kind", "created_at"])
    op.create_index("ix_c001_abuse_browser_kind_time", "c001_abuse_events", ["browser_key", "kind", "created_at"])
    op.create_index("ix_c001_abuse_expiry", "c001_abuse_events", ["created_at"])


def downgrade():
    op.drop_table("c001_abuse_events")
    op.drop_table("c001_activation_control")
    op.drop_column("child_pending_consents", "cohort_source")
    op.drop_column("tester_enrollments", "source")
