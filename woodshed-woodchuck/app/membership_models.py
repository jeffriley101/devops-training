"""Billing identities and access history; independent of verifier relationships."""
from datetime import datetime, timezone
from sqlalchemy import (Integer, String, DateTime, Boolean, ForeignKey, CheckConstraint,
                        UniqueConstraint, Index, JSON, text)
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def now():
    return datetime.now(timezone.utc)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)


class BillingAccount(Timestamps, Base):
    __tablename__ = "billing_accounts"
    __table_args__ = (CheckConstraint(
        "(profile_id IS NOT NULL AND verifier_id IS NULL) OR (profile_id IS NULL AND verifier_id IS NOT NULL)",
        name="ck_billing_account_one_owner"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT"), unique=True)
    verifier_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), unique=True)


class Membership(Timestamps, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'ended', 'revoked')", name="ck_membership_status"),
        CheckConstraint("source IN ('manual', 'paypal', 'stripe')", name="ck_membership_source"),
        CheckConstraint("max_student_seats = 5", name="ck_membership_five_seats"),
        CheckConstraint("access_until IS NULL OR access_until > starts_at", name="ck_membership_dates"),
        Index("uq_membership_active_owner", "billing_account_id", unique=True,
              sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    billing_account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    source: Mapped[str] = mapped_column(String(20))
    plan_code: Mapped[str | None] = mapped_column(String(50))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Paid memberships: confirmed entitlement only, never provider period metadata.
    # Manual complimentary memberships may have an optional administrator-set end.
    access_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_student_seats: Mapped[int] = mapped_column(Integer, default=5)


class MembershipSeat(Base):
    __tablename__ = "membership_seats"
    __table_args__ = (
        CheckConstraint("slot_number >= 1 AND slot_number <= 5", name="ck_membership_seat_slot"),
        Index("uq_membership_seat_active_student", "profile_id", unique=True,
              sqlite_where=text("removed_at IS NULL"), postgresql_where=text("removed_at IS NULL")),
        Index("uq_membership_seat_active_slot", "membership_id", "slot_number", unique=True,
              sqlite_where=text("removed_at IS NULL"), postgresql_where=text("removed_at IS NULL")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("memberships.id", ondelete="RESTRICT"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT"), index=True)
    slot_number: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MembershipSeatInvitation(Timestamps, Base):
    __tablename__ = "membership_seat_invitations"
    __table_args__ = (CheckConstraint("status IN ('pending', 'accepted', 'cancelled', 'expired')",
                                    name="ck_membership_invitation_status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("memberships.id", ondelete="RESTRICT"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_profile_id: Mapped[int | None] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT"))


class ProviderSubscription(Timestamps, Base):
    __tablename__ = "provider_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider", "external_subscription_id", name="uq_provider_subscription"),
        CheckConstraint("provider IN ('paypal', 'stripe')", name="ck_subscription_provider"),
        CheckConstraint("amount_cents >= 0", name="ck_subscription_amount"),
        CheckConstraint("interval IN ('month', 'year')", name="ck_subscription_interval"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("memberships.id", ondelete="RESTRICT"), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    external_customer_id: Mapped[str | None] = mapped_column(String(255))
    external_subscription_id: Mapped[str] = mapped_column(String(255))
    plan_code: Mapped[str] = mapped_column(String(50))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    interval: Mapped[str] = mapped_column(String(10))
    provider_status: Mapped[str] = mapped_column(String(50))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingProviderEvent(Base):
    __tablename__ = "billing_provider_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_billing_provider_event"),
        CheckConstraint("provider IN ('paypal', 'stripe')", name="ck_billing_event_provider"),
        CheckConstraint("status IN ('received', 'processed', 'ignored', 'failed')", name="ck_billing_event_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    external_event_id: Mapped[str] = mapped_column(String(255))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="received")
    payload_hash: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(40))


class MembershipAuditEvent(Base):
    __tablename__ = "membership_audit_events"
    __table_args__ = (CheckConstraint("membership_id IS NOT NULL OR billing_event_id IS NOT NULL",
                                     name="ck_membership_audit_target"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    membership_id: Mapped[int | None] = mapped_column(ForeignKey("memberships.id", ondelete="RESTRICT"), index=True)
    billing_event_id: Mapped[int | None] = mapped_column(ForeignKey("billing_provider_events.id", ondelete="RESTRICT",
        name="fk_membership_audit_billing_event"), index=True)
    action: Mapped[str] = mapped_column(String(50))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CheckoutAttempt(Timestamps, Base):
    __tablename__ = "checkout_attempts"
    __table_args__ = (
        CheckConstraint("provider IN ('paypal', 'stripe')", name="ck_checkout_provider"),
        CheckConstraint("status IN ('pending', 'completed', 'expired', 'failed', 'recoverable')", name="ck_checkout_status"),
        CheckConstraint("expires_at > created_at", name="ck_checkout_expiry"),
        CheckConstraint("amount_cents > 0", name="ck_checkout_amount"),
        CheckConstraint("interval IN ('month', 'year')", name="ck_checkout_interval"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    billing_account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    plan_code: Mapped[str] = mapped_column(String(50))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    interval: Mapped[str] = mapped_column(String(10))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider_checkout_id: Mapped[str | None] = mapped_column(String(255))
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("provider_subscriptions.id", ondelete="RESTRICT"), unique=True)


class BillingEventApplication(Base):
    """Minimal verified facts for explicit retry; never raw payment payloads."""
    __tablename__ = "billing_event_applications"
    __table_args__ = (CheckConstraint("status IN ('pending', 'recoverable', 'processed')", name="ck_event_application_status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("billing_provider_events.id", ondelete="RESTRICT"), unique=True)
    external_subscription_id: Mapped[str] = mapped_column(String(255), index=True)
    checkout_reference: Mapped[str | None] = mapped_column(String(64), index=True)
    facts: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class BillingPaymentEffect(Base):
    __tablename__ = "billing_payment_effects"
    __table_args__ = (UniqueConstraint("subscription_id", "payment_reference", name="uq_subscription_payment_effect"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("provider_subscriptions.id", ondelete="RESTRICT"))
    payment_reference: Mapped[str] = mapped_column(String(255))
    paid_through: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[int] = mapped_column(ForeignKey("billing_provider_events.id", ondelete="RESTRICT"))
