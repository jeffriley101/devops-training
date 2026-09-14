"""Audit recovery before a membership exists; preserve existing audit history."""
from alembic import op
import sqlalchemy as sa

revision = "q7l8m9n0o1p2"
down_revision = "p6k7l8m9n0o1"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("membership_audit_events") as batch:
        batch.alter_column("membership_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("billing_event_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_membership_audit_billing_event", "billing_provider_events",
                                 ["billing_event_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_membership_audit_events_billing_event_id", ["billing_event_id"])
        batch.create_check_constraint("ck_membership_audit_target",
                                      "membership_id IS NOT NULL OR billing_event_id IS NOT NULL")


def downgrade():
    # The previous schema cannot retain event-only audit targets or their FK.
    # Refuse a lossy downgrade once recovery has been used; never delete audits
    # or invent placeholder memberships to make a downgrade succeed.
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE membership_audit_events IN ACCESS EXCLUSIVE MODE"))
    elif connection.dialect.name == "sqlite":
        connection.execute(sa.text("UPDATE membership_audit_events SET id = id WHERE 0"))
    if connection.scalar(sa.text("SELECT COUNT(*) FROM membership_audit_events "
                                    "WHERE membership_id IS NULL OR billing_event_id IS NOT NULL")):
        raise RuntimeError("Cannot downgrade while billing recovery audit history exists.")
    with op.batch_alter_table("membership_audit_events") as batch:
        batch.drop_constraint("ck_membership_audit_target", type_="check")
        batch.drop_index("ix_membership_audit_events_billing_event_id")
        batch.drop_constraint("fk_membership_audit_billing_event", type_="foreignkey")
        batch.drop_column("billing_event_id")
        batch.alter_column("membership_id", existing_type=sa.Integer(), nullable=False)
