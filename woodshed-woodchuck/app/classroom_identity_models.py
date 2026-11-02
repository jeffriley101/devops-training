"""Program-private school identity and PII-minimal change evidence."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ClassroomRosterIdentity(Base):
    __tablename__ = 'classroom_roster_identities'
    __table_args__ = (
        UniqueConstraint('program_id', 'profile_id', name='uq_classroom_identity_program_profile'),
        UniqueConstraint('id', 'program_id', 'profile_id', name='uq_classroom_identity_scope'),
        CheckConstraint('length(trim(school_name)) BETWEEN 1 AND 150 AND school_name = trim(school_name)', name='ck_classroom_identity_name'),
        CheckConstraint('version >= 1', name='ck_classroom_identity_version'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey('classroom_programs.organization_id', ondelete='RESTRICT'), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id', ondelete='RESTRICT'), nullable=False)
    school_name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ClassroomRosterIdentityAudit(Base):
    __tablename__ = 'classroom_roster_identity_audits'
    __table_args__ = (
        ForeignKeyConstraint(['identity_id', 'program_id', 'profile_id'],
            ['classroom_roster_identities.id', 'classroom_roster_identities.program_id', 'classroom_roster_identities.profile_id'],
            ondelete='RESTRICT', name='fk_classroom_identity_audit_scope'),
        ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'],
            ondelete='RESTRICT', name='fk_classroom_identity_audit_class'),
        UniqueConstraint('identity_id', 'version', name='uq_classroom_identity_audit_version'),
        CheckConstraint('version >= 1', name='ck_classroom_identity_audit_version'),
        CheckConstraint("(action = 'created' AND version = 1) OR (action = 'corrected' AND version > 1)", name='ck_classroom_identity_audit_action'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_verifier_id: Mapped[int] = mapped_column(ForeignKey('trusted_verifiers.id', ondelete='RESTRICT'), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
