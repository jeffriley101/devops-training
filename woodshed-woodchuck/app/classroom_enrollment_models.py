"""Enrollment offers and lifecycle evidence; never reporting or sharing grants."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ClassroomEnrollmentInvitation(Base):
    __tablename__ = 'classroom_enrollment_invitations'
    __table_args__ = (
        UniqueConstraint('id', 'program_id', 'class_id', 'profile_id', name='uq_classroom_enrollment_invitation_scope'),
        ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'],
                             ondelete='RESTRICT', name='fk_classroom_enrollment_class'),
        ForeignKeyConstraint(['assignment_id', 'class_id', 'program_id'],
            ['classroom_teaching_assignments.id', 'classroom_teaching_assignments.class_id', 'classroom_teaching_assignments.program_id'],
            ondelete='RESTRICT', name='fk_classroom_enrollment_assignment'),
        CheckConstraint("purpose = 'classroom-enrollment-v2'", name='ck_classroom_enrollment_purpose'),
        CheckConstraint("state IN ('pending', 'deferred', 'accepted', 'declined', 'expired', 'revoked')", name='ck_classroom_enrollment_state'),
        CheckConstraint('expires_at > issued_at', name='ck_classroom_enrollment_expiry'),
        CheckConstraint("(state IN ('pending', 'deferred') AND resolved_at IS NULL AND accepted_period_id IS NULL) OR "
            "(state IN ('accepted', 'declined', 'expired', 'revoked') AND resolved_at IS NOT NULL AND resolved_at >= issued_at)",
            name='ck_classroom_enrollment_resolution'),
        CheckConstraint("(state = 'accepted' AND accepted_period_id IS NOT NULL) OR (state <> 'accepted' AND accepted_period_id IS NULL)",
                        name='ck_classroom_enrollment_acceptance'),
        CheckConstraint("state NOT IN ('accepted', 'declined') OR resolved_at < expires_at", name='ck_classroom_enrollment_response_time'),
        CheckConstraint("state <> 'expired' OR resolved_at >= expires_at", name='ck_classroom_enrollment_expired_time'),
        Index('uq_classroom_enrollment_invitation_open', 'assignment_id', 'profile_id', unique=True,
              sqlite_where=text('resolved_at IS NULL'), postgresql_where=text('resolved_at IS NULL')),
        Index('ix_classroom_enrollment_student_state', 'profile_id', 'state'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    program_id: Mapped[int] = mapped_column(ForeignKey('classroom_programs.organization_id', ondelete='RESTRICT'), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id', ondelete='RESTRICT'), nullable=False)
    issued_by_verifier_id: Mapped[int] = mapped_column(ForeignKey('trusted_verifiers.id', ondelete='RESTRICT'), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_period_id: Mapped[int | None] = mapped_column(ForeignKey('classroom_membership_periods.id', ondelete='RESTRICT'))


class ClassroomEnrollmentAuditEvent(Base):
    __tablename__ = 'classroom_enrollment_audit_events'
    __table_args__ = (
        ForeignKeyConstraint(['invitation_id', 'program_id', 'class_id', 'profile_id'],
            ['classroom_enrollment_invitations.id', 'classroom_enrollment_invitations.program_id',
             'classroom_enrollment_invitations.class_id', 'classroom_enrollment_invitations.profile_id'],
            ondelete='RESTRICT', name='fk_classroom_enrollment_audit_scope'),
        UniqueConstraint('invitation_id', 'action', name='uq_classroom_enrollment_audit_action'),
        CheckConstraint("action IN ('issued', 'accepted', 'declined', 'deferred', 'expired', 'revoked')", name='ck_classroom_enrollment_audit_action'),
        CheckConstraint("(actor_profile_id IS NOT NULL AND actor_profile_id = profile_id AND actor_verifier_id IS NULL "
            "AND action IN ('accepted', 'declined', 'deferred', 'expired')) OR "
            "(actor_verifier_id IS NOT NULL AND actor_profile_id IS NULL AND action IN ('issued', 'revoked', 'expired'))",
            name='ck_classroom_enrollment_audit_actor'),
        CheckConstraint("(action = 'accepted' AND membership_period_id IS NOT NULL) OR "
            "(action <> 'accepted' AND membership_period_id IS NULL)", name='ck_classroom_enrollment_audit_period'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invitation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    actor_profile_id: Mapped[int | None] = mapped_column(ForeignKey('woodchuck_profiles.id', ondelete='RESTRICT'))
    actor_verifier_id: Mapped[int | None] = mapped_column(ForeignKey('trusted_verifiers.id', ondelete='RESTRICT'))
    membership_period_id: Mapped[int | None] = mapped_column(ForeignKey('classroom_membership_periods.id', ondelete='RESTRICT'))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
