"""Add opt-in Class-specific and exact-director P-Chart sharing storage.

No backfill, comprehensive reporting authorization or feature enablement.
"""
from alembic import op
import sqlalchemy as sa

revision = "c25class001"
down_revision = "w25weekly005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "classroom_chart_shares",
        sa.Column("chart_id", sa.Integer(), sa.ForeignKey("practice_charts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classroom_classes.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "classroom_chart_director_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chart_id", sa.Integer(), sa.ForeignKey("practice_charts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classroom_classes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assignment_id", sa.Integer(), sa.ForeignKey("classroom_teaching_assignments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("membership_period_id", sa.Integer(), sa.ForeignKey("classroom_membership_periods.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), sa.ForeignKey("classroom_entitlements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_declared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("class_activation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_audit_id", sa.Integer(), sa.ForeignKey("classroom_s2_audit_events.id", ondelete="RESTRICT")),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("withdrawn_at IS NULL OR withdrawn_at >= selected_at", name="ck_classroom_chart_director_share_end"),
    )
    op.create_index("uq_classroom_chart_director_share_open", "classroom_chart_director_shares",
                    ["chart_id", "assignment_id"], unique=True,
                    sqlite_where=sa.text("withdrawn_at IS NULL"), postgresql_where=sa.text("withdrawn_at IS NULL"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE classroom_chart_shares, classroom_chart_director_shares IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE classroom_chart_shares SET chart_id = chart_id WHERE 0"))
        bind.execute(sa.text("UPDATE classroom_chart_director_shares SET id = id WHERE 0"))
    else:
        raise RuntimeError("classroom_chart_shares_unsupported_database")
    if (bind.scalar(sa.text("SELECT count(*) FROM classroom_chart_shares"))
            or bind.scalar(sa.text("SELECT count(*) FROM classroom_chart_director_shares"))):
        raise RuntimeError("classroom_chart_shares_in_use: downgrade refused")
    op.drop_index("uq_classroom_chart_director_share_open", table_name="classroom_chart_director_shares")
    op.drop_table("classroom_chart_director_shares")
    op.drop_table("classroom_chart_shares")
