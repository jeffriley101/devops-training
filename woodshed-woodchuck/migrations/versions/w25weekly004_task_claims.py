"""Add only BOARD/Thirds claim receipts; no backfill or reward settlement."""
from alembic import op
import sqlalchemy as sa

revision = "w25weekly004"
down_revision = "w25weekly003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("weekly_task_claims",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("weekly_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lane", sa.String(10), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("plan_id", "lane", name="pk_weekly_task_claim"),
        sa.CheckConstraint("lane IN ('board', 'thirds')", name="ck_weekly_task_claim_lane"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE weekly_task_claims IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE weekly_task_claims SET plan_id = plan_id WHERE 0"))
    else:
        raise RuntimeError("weekly_claims_unsupported_database")
    if bind.scalar(sa.text("SELECT count(*) FROM weekly_task_claims")):
        raise RuntimeError("weekly_claims_in_use: downgrade refused")
    op.drop_table("weekly_task_claims")
