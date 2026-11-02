"""Exact-Class guardian choices; no parent identity, KWS result or school authority issuance."""
from datetime import datetime

from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, String, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ClassroomGuardianAuthorization(Base):
    __tablename__ = 'classroom_guardian_authorizations'
    __table_args__ = (
        ForeignKeyConstraint(['class_id', 'program_id'],
            ['classroom_classes.id', 'classroom_classes.program_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['participation_id', 'program_id', 'class_id', 'profile_id'],
            ['classroom_guardian_authorizations.id', 'classroom_guardian_authorizations.program_id',
             'classroom_guardian_authorizations.class_id', 'classroom_guardian_authorizations.profile_id'],
            ondelete='RESTRICT', name='fk_classroom_guardian_participation_scope'),
        UniqueConstraint('id', 'program_id', 'class_id', 'profile_id', name='uq_classroom_guardian_scope'),
        CheckConstraint("purpose IN ('participation', 'reporting')", name='ck_classroom_guardian_purpose'),
        CheckConstraint("(purpose = 'participation' AND field_contract IS NULL AND participation_id IS NULL) OR "
            "(purpose = 'reporting' AND field_contract IS NOT NULL AND field_contract = 'classroom-reporting-41-v1' AND participation_id IS NOT NULL)",
            name='ck_classroom_guardian_field_contract'),
        CheckConstraint('expires_at > accepted_at AND guardian_attested_at = accepted_at AND notice_accepted_at = accepted_at',
            name='ck_classroom_guardian_dates'),
        CheckConstraint("(withdrawn_at IS NULL AND withdrawal_reason IS NULL) OR "
            "(withdrawn_at IS NOT NULL AND withdrawal_reason IS NOT NULL AND withdrawn_at >= accepted_at AND withdrawal_reason IN ('withdrawn', 'replaced'))",
            name='ck_classroom_guardian_withdrawal'),
        Index('uq_classroom_guardian_open', 'program_id', 'class_id', 'profile_id', 'purpose', unique=True,
            sqlite_where=text('withdrawn_at IS NULL'), postgresql_where=text('withdrawn_at IS NULL')),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id', ondelete='RESTRICT'), nullable=False)
    consent_id: Mapped[int] = mapped_column(ForeignKey('child_consent_evidence.id', ondelete='RESTRICT'), nullable=False)
    verification_id: Mapped[int] = mapped_column(ForeignKey('child_kws_verifications.id', ondelete='RESTRICT'), nullable=False)
    membership_period_id: Mapped[int] = mapped_column(ForeignKey('classroom_membership_periods.id', ondelete='RESTRICT'), nullable=False)
    entitlement_id: Mapped[int] = mapped_column(ForeignKey('classroom_entitlements.id', ondelete='RESTRICT'), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    participation_id: Mapped[int | None] = mapped_column(Integer)
    field_contract: Mapped[str | None] = mapped_column(String(80))
    notice_version: Mapped[str] = mapped_column(String(80), nullable=False)
    notice_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attestation_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    guardian_attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notice_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawal_reason: Mapped[str | None] = mapped_column(String(16))


class ClassroomGuardianRecipient(Base):
    __tablename__ = 'classroom_guardian_recipients'
    __table_args__ = (
        ForeignKeyConstraint(['authorization_id', 'program_id', 'class_id', 'profile_id'],
            ['classroom_guardian_authorizations.id', 'classroom_guardian_authorizations.program_id',
             'classroom_guardian_authorizations.class_id', 'classroom_guardian_authorizations.profile_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['assignment_id', 'class_id', 'program_id'],
            ['classroom_teaching_assignments.id', 'classroom_teaching_assignments.class_id',
             'classroom_teaching_assignments.program_id'], ondelete='RESTRICT'),
    )
    authorization_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False)


class ClassroomGuardianAudit(Base):
    __tablename__ = 'classroom_guardian_audits'
    __table_args__ = (
        ForeignKeyConstraint(['authorization_id', 'program_id', 'class_id', 'profile_id'],
            ['classroom_guardian_authorizations.id', 'classroom_guardian_authorizations.program_id',
             'classroom_guardian_authorizations.class_id', 'classroom_guardian_authorizations.profile_id'], ondelete='RESTRICT'),
        CheckConstraint("action IN ('accepted', 'withdrawn', 'replaced')", name='ck_classroom_guardian_audit_action'),
        UniqueConstraint('authorization_id', 'action', name='uq_classroom_guardian_audit_action'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    authorization_id: Mapped[int] = mapped_column(Integer, nullable=False)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_consent_id: Mapped[int] = mapped_column(ForeignKey('child_consent_evidence.id', ondelete='RESTRICT'), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
