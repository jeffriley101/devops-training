"""Audit provider inspections before a checkout has any billing event."""
from alembic import op
import sqlalchemy as sa

revision = "r8m9n0o1p2q3"
down_revision = "q7l8m9n0o1p2"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("membership_audit_events") as batch:
        batch.add_column(sa.Column("checkout_attempt_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_membership_audit_checkout_attempt", "checkout_attempts",
                                 ["checkout_attempt_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_membership_audit_events_checkout_attempt_id", ["checkout_attempt_id"])
        batch.drop_constraint("ck_membership_audit_target", type_="check")
        batch.create_check_constraint("ck_membership_audit_target",
            "membership_id IS NOT NULL OR billing_event_id IS NOT NULL OR checkout_attempt_id IS NOT NULL")


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE membership_audit_events IN ACCESS EXCLUSIVE MODE"))
    elif connection.dialect.name == "sqlite":
        connection.execute(sa.text("UPDATE membership_audit_events SET id = id WHERE 0"))
    if connection.scalar(sa.text("SELECT COUNT(*) FROM membership_audit_events WHERE checkout_attempt_id IS NOT NULL")):
        raise RuntimeError("Cannot downgrade while checkout inspection audit history exists.")
    with op.batch_alter_table("membership_audit_events") as batch:
        batch.drop_constraint("ck_membership_audit_target", type_="check")
        batch.drop_index("ix_membership_audit_events_checkout_attempt_id")
        batch.drop_constraint("fk_membership_audit_checkout_attempt", type_="foreignkey")
        batch.drop_column("checkout_attempt_id")
        batch.create_check_constraint("ck_membership_audit_target",
                                      "membership_id IS NOT NULL OR billing_event_id IS NOT NULL")
