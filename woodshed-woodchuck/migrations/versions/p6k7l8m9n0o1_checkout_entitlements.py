"""Durable checkout authorization and retryable verified payment facts.

Only new tables; existing paid-through, accounts and history are unchanged.
"""
from alembic import op
import sqlalchemy as sa

revision = "p6k7l8m9n0o1"
down_revision = "o5j6k7l8m9n0"
branch_labels = depends_on = None


def upgrade():
    op.create_table("checkout_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(64), nullable=False, unique=True),
        sa.Column("billing_account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("plan_code", sa.String(50), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("interval", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider_checkout_id", sa.String(255)),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("provider_subscriptions.id", ondelete="RESTRICT"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('paypal', 'stripe')", name="ck_checkout_provider"),
        sa.CheckConstraint("status IN ('pending', 'completed', 'expired', 'failed', 'recoverable')", name="ck_checkout_status"),
        sa.CheckConstraint("expires_at > created_at", name="ck_checkout_expiry"),
        sa.CheckConstraint("amount_cents > 0", name="ck_checkout_amount"),
        sa.CheckConstraint("interval IN ('month', 'year')", name="ck_checkout_interval"))
    op.create_index("ix_checkout_attempts_billing_account_id", "checkout_attempts", ["billing_account_id"])
    op.create_table("billing_event_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("billing_provider_events.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("external_subscription_id", sa.String(255), nullable=False),
        sa.Column("checkout_reference", sa.String(64)),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(40)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'recoverable', 'processed')", name="ck_event_application_status"))
    for column in ("external_subscription_id", "checkout_reference"):
        op.create_index(f"ix_billing_event_applications_{column}", "billing_event_applications", [column])
    op.create_table("billing_payment_effects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("provider_subscriptions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_reference", sa.String(255), nullable=False),
        sa.Column("paid_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("billing_provider_events.id", ondelete="RESTRICT"), nullable=False),
        sa.UniqueConstraint("subscription_id", "payment_reference", name="uq_subscription_payment_effect"))


def downgrade():
    for table in ("billing_payment_effects", "billing_event_applications", "checkout_attempts"):
        op.drop_table(table)
