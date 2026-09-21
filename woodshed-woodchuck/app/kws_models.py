"""Test verification bindings and durable email/replay protection. No card data."""
from datetime import datetime
from sqlalchemy import String, ForeignKey, DateTime, Boolean, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class KWSVerification(Base):
    __tablename__ = 'child_kws_verifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    pending_id: Mapped[int] = mapped_column(ForeignKey('child_pending_consents.id'), unique=True, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(128))
    binding_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    notice_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_session_version: Mapped[int | None] = mapped_column(Integer)
    prior_consent_id: Mapped[int | None] = mapped_column(Integer)
    guardian_attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notice_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    account_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    director_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    transaction_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_consent_id: Mapped[int | None] = mapped_column(ForeignKey('child_consent_evidence.id'))


class KWSEmailBudget(Base):
    __tablename__ = 'child_kws_email_budgets'
    email_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    sent_at: Mapped[list] = mapped_column(JSON, nullable=False)
