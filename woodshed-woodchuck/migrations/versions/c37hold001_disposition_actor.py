"""Allow disposition of historical membership hold attribution."""
from alembic import op
import sqlalchemy as sa

revision = "c37hold001"
down_revision = "c36choice001"
branch_labels = None
depends_on = None


def _change(nullable):
    with op.batch_alter_table("classroom_membership_holds") as batch:
        batch.alter_column("held_by_verifier_id", existing_type=sa.Integer(), nullable=nullable)


def upgrade():
    from app import classroom_hold_guard
    bind = op.get_bind()
    if bind.dialect.name == "postgresql" and bind.exec_driver_sql("SELECT current_schema()").scalar_one() != "public":
        raise RuntimeError("C37 hold guard requires the public application schema.")
    _change(True)
    classroom_hold_guard.install(bind)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("LOCK TABLE classroom_membership_holds IN ACCESS EXCLUSIVE MODE")
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM classroom_membership_holds WHERE held_by_verifier_id IS NULL)")):
        raise RuntimeError("hold_attribution_disposed: downgrade refused")
    from app import classroom_hold_guard
    classroom_hold_guard.uninstall(bind)
    _change(False)
