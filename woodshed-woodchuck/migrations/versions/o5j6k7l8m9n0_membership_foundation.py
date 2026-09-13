"""Provider-neutral membership foundation; no existing records are modified.

Revision ID: o5j6k7l8m9n0
Revises: n4i5j6k7l8m9
"""
from alembic import op
import sqlalchemy as sa

revision = "o5j6k7l8m9n0"
down_revision = "n4i5j6k7l8m9"
branch_labels = depends_on = None


def pk():
    return sa.Column("id", sa.Integer(), primary_key=True)


def stamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def timestamps():
    return [stamp("created_at"), stamp("updated_at")]


def fk(name, target, nullable=False, unique=False):
    return sa.Column(name, sa.Integer(), sa.ForeignKey(target, ondelete="RESTRICT"), nullable=nullable, unique=unique)


def string(name, length, nullable=False):
    return sa.Column(name, sa.String(length), nullable=nullable)


def active_index(name, table, columns, predicate):
    op.create_index(name, table, columns, unique=True,
                    sqlite_where=sa.text(predicate), postgresql_where=sa.text(predicate))


def upgrade():
    op.create_table("billing_accounts", pk(),
        fk("profile_id", "woodchuck_profiles.id", True, True),
        fk("verifier_id", "trusted_verifiers.id", True, True), *timestamps(),
        sa.CheckConstraint("(profile_id IS NOT NULL AND verifier_id IS NULL) OR (profile_id IS NULL AND verifier_id IS NOT NULL)",
                           name="ck_billing_account_one_owner"))
    op.create_table("memberships", pk(), fk("billing_account_id", "billing_accounts.id"),
        string("status", 20), string("source", 20), string("plan_code", 50, True),
        stamp("starts_at"), stamp("access_until", True), stamp("revoked_at", True),
        sa.Column("max_student_seats", sa.Integer(), nullable=False), *timestamps(),
        sa.CheckConstraint("status IN ('active', 'ended', 'revoked')", name="ck_membership_status"),
        sa.CheckConstraint("source IN ('manual', 'paypal', 'stripe')", name="ck_membership_source"),
        sa.CheckConstraint("max_student_seats = 5", name="ck_membership_five_seats"),
        sa.CheckConstraint("access_until IS NULL OR access_until > starts_at", name="ck_membership_dates"))
    op.create_index("ix_memberships_billing_account_id", "memberships", ["billing_account_id"])
    active_index("uq_membership_active_owner", "memberships", ["billing_account_id"], "status = 'active'")
    op.create_table("membership_seats", pk(), fk("membership_id", "memberships.id"),
        fk("profile_id", "woodchuck_profiles.id"), sa.Column("slot_number", sa.Integer(), nullable=False),
        stamp("added_at"), stamp("removed_at", True),
        sa.CheckConstraint("slot_number >= 1 AND slot_number <= 5", name="ck_membership_seat_slot"))
    for column in ("membership_id", "profile_id"):
        op.create_index(f"ix_membership_seats_{column}", "membership_seats", [column])
    active_index("uq_membership_seat_active_student", "membership_seats", ["profile_id"], "removed_at IS NULL")
    active_index("uq_membership_seat_active_slot", "membership_seats", ["membership_id", "slot_number"], "removed_at IS NULL")
    op.create_table("membership_seat_invitations", pk(), fk("membership_id", "memberships.id"),
        string("email", 320), sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        string("status", 20), stamp("expires_at"), stamp("accepted_at", True),
        fk("accepted_profile_id", "woodchuck_profiles.id", True), *timestamps(),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'cancelled', 'expired')", name="ck_membership_invitation_status"))
    op.create_index("ix_membership_seat_invitations_membership_id", "membership_seat_invitations", ["membership_id"])
    op.create_table("provider_subscriptions", pk(), fk("membership_id", "memberships.id", unique=True),
        string("provider", 20), string("external_customer_id", 255, True), string("external_subscription_id", 255),
        string("plan_code", 50), sa.Column("amount_cents", sa.Integer(), nullable=False), string("currency", 3),
        string("interval", 10), string("provider_status", 50),
        stamp("current_period_start", True), stamp("current_period_end", True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False), stamp("last_event_at", True),
        stamp("terminated_at", True), *timestamps(),
        sa.UniqueConstraint("provider", "external_subscription_id", name="uq_provider_subscription"),
        sa.CheckConstraint("provider IN ('paypal', 'stripe')", name="ck_subscription_provider"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_subscription_amount"),
        sa.CheckConstraint("interval IN ('month', 'year')", name="ck_subscription_interval"))
    op.create_table("billing_provider_events", pk(), string("provider", 20), string("external_event_id", 255),
        stamp("received_at"), stamp("processed_at", True), string("status", 20),
        string("payload_hash", 64), string("error_code", 40, True),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_billing_provider_event"),
        sa.CheckConstraint("provider IN ('paypal', 'stripe')", name="ck_billing_event_provider"),
        sa.CheckConstraint("status IN ('received', 'processed', 'ignored', 'failed')", name="ck_billing_event_status"))
    op.create_table("membership_audit_events", pk(), fk("membership_id", "memberships.id"),
        string("action", 50), string("actor_type", 20), sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False), stamp("created_at"))
    op.create_index("ix_membership_audit_events_membership_id", "membership_audit_events", ["membership_id"])


def downgrade():
    # Only these new structures are removed. Practice, relationships, teams,
    # results and rewards are never rewritten by either direction.
    for table in ("membership_audit_events", "billing_provider_events", "provider_subscriptions",
                  "membership_seat_invitations", "membership_seats", "memberships", "billing_accounts"):
        op.drop_table(table)
