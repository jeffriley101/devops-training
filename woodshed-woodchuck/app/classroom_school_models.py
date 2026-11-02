"""Retained independent verification and one annual Program school agreement."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ClassroomSchoolAuthorization(Base):
    __tablename__ = 'classroom_school_authorizations'
    __table_args__ = (
        CheckConstraint("authority_kind IN ('director', 'administrator')", name='ck_classroom_school_kind'),
        CheckConstraint("verification_method IN ('phone', 'official_email')", name='ck_classroom_school_method'),
        CheckConstraint('year_start BETWEEN 2000 AND 9998', name='ck_classroom_school_year'),
        CheckConstraint('approver_id <> verified_by_id', name='ck_classroom_school_independent'),
        CheckConstraint('contacted_at <= verified_at AND verified_at < expires_at', name='ck_classroom_school_verification_dates'),
        CheckConstraint('approved_at IS NULL OR (approved_at >= verified_at AND approved_at < expires_at)', name='ck_classroom_school_approval_date'),
        CheckConstraint("(revoked_at IS NULL AND revoked_by_id IS NULL AND revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_at >= verified_at AND revoked_by_id IS NOT NULL "
            "AND revocation_reason IS NOT NULL AND revocation_reason IN ('withdrawn', 'authority_revoked', 'replaced'))",
            name='ck_classroom_school_revocation'),
        Index('uq_classroom_school_open_year', 'program_id', 'year_start', unique=True,
            sqlite_where=text('revoked_at IS NULL'), postgresql_where=text('revoked_at IS NULL')),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey('classroom_programs.organization_id', ondelete='RESTRICT'), nullable=False)
    approver_id: Mapped[int] = mapped_column(ForeignKey('trusted_verifiers.id', ondelete='RESTRICT'), nullable=False)
    authority_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    school_name: Mapped[str] = mapped_column(String(150), nullable=False)
    authority_description: Mapped[str] = mapped_column(String(500), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_role: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_address: Mapped[str] = mapped_column(String(254), nullable=False)
    contact_source: Mapped[str] = mapped_column(String(500), nullable=False)
    verification_method: Mapped[str] = mapped_column(String(20), nullable=False)
    verification_notes: Mapped[str] = mapped_column(String(1000), nullable=False)
    contacted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_by_id: Mapped[int] = mapped_column(ForeignKey('trusted_verifiers.id', ondelete='RESTRICT'), nullable=False)
    verified_admin_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    year_start: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    agreement_version: Mapped[str] = mapped_column(String(80), nullable=False)
    agreement_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[int | None] = mapped_column(ForeignKey('trusted_verifiers.id', ondelete='RESTRICT'))
    revocation_reason: Mapped[str | None] = mapped_column(String(24))
