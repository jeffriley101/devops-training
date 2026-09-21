"""Age/privacy and consent are account rules, never subscription tiers."""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, CheckConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def now(): return datetime.now(timezone.utc)

class PendingConsent(Base):
    __tablename__ = 'child_pending_consents'
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey('woodchuck_profiles.id'))
    parent_email: Mapped[str] = mapped_column(String(254), nullable=False)
    director_email: Mapped[str] = mapped_column(String(254), nullable=False)
    director_name: Mapped[str] = mapped_column(String(80), nullable=False)
    review_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approve_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    activation_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notice_version: Mapped[str] = mapped_column(String(80), nullable=False)

class ConsentEvidence(Base):
    __tablename__ = 'child_consent_evidence'
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id'), nullable=False, index=True)
    parent_email: Mapped[str | None] = mapped_column(String(254))
    activation_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    withdrawal_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    notice_version: Mapped[str] = mapped_column(String(80), nullable=False)
    notice_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

class ParentAccess(Base):
    """One bounded access credential per consent; no new parent account or email copy."""
    __tablename__ = 'child_parent_access'
    consent_id: Mapped[int] = mapped_column(ForeignKey('child_consent_evidence.id', ondelete='CASCADE'), primary_key=True)
    link_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verifier_id: Mapped[int | None] = mapped_column(ForeignKey('trusted_verifiers.id'))

class ParentAgeDeclaration(Base):
    __tablename__ = 'child_parent_age_declarations'
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id'), primary_key=True)
    consent_id: Mapped[int | None] = mapped_column(ForeignKey('child_consent_evidence.id', ondelete='SET NULL'))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wording_version: Mapped[str] = mapped_column(String(80), nullable=False)

class DirectorPermission(Base):
    __tablename__ = 'child_director_permissions'
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id'), nullable=False)
    consent_id: Mapped[int] = mapped_column(ForeignKey('child_consent_evidence.id'), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    director_name: Mapped[str] = mapped_column(String(80), nullable=False)
    review_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey('student_verifier_connections.id'))
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    link_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
