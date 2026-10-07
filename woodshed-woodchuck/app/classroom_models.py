"""Explicit Classroom relationships; none of these records grants student access.

Historical Organization provenance and free verifier relationships remain separate.
Every authority record uses restrictive foreign keys so deleting an identity cannot
silently erase ownership, revocation, or audit evidence.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
    String, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClassroomProgram(Base):
    __tablename__ = "classroom_programs"
    __table_args__ = (
        CheckConstraint("owner_version >= 1", name="ck_classroom_program_owner_version"),
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), primary_key=True,
    )
    owner_verifier_id: Mapped[int] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    owner_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False,
    )


class ClassroomRoleGrant(Base):
    __tablename__ = "classroom_role_grants"
    __table_args__ = (
        UniqueConstraint("id", "program_id", name="uq_classroom_role_id_program"),
        CheckConstraint(
            "role IN ('head_director', 'admin', 'code_manager', 'billing')",
            name="ck_classroom_role_kind",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at > starts_at", name="ck_classroom_role_period"),
        CheckConstraint(
            "(ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_by_verifier_id IS NOT NULL AND ended_reason IS NOT NULL AND "
            "ended_reason IN ('revoked', 'departed'))", name="ck_classroom_role_end_evidence",
        ),
        Index("uq_classroom_role_open", "program_id", "verifier_id", "role", unique=True,
              sqlite_where=text("ended_at IS NULL"), postgresql_where=text("ended_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("classroom_programs.organization_id", ondelete="RESTRICT"), nullable=False,
    )
    verifier_id: Mapped[int] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_reason: Mapped[str | None] = mapped_column(String(10))
    granted_by_verifier_id: Mapped[int] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False,
    )
    ended_by_verifier_id: Mapped[int | None] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"),
    )


class ClassroomClass(Base):
    __tablename__ = "classroom_classes"
    __table_args__ = (
        UniqueConstraint("id", "program_id", name="uq_classroom_class_id_program"),
        CheckConstraint("length(trim(display_name)) BETWEEN 1 AND 150", name="ck_classroom_class_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("classroom_programs.organization_id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False,
    )


class ClassroomTeachingAssignment(Base):
    __tablename__ = "classroom_teaching_assignments"
    __table_args__ = (
        UniqueConstraint("id", "class_id", "program_id", name="uq_classroom_assignment_scope"),
        ForeignKeyConstraint(
            ["class_id", "program_id"], ["classroom_classes.id", "classroom_classes.program_id"],
            ondelete="RESTRICT", name="fk_classroom_assignment_class_program",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at > starts_at", name="ck_classroom_assignment_period"),
        CheckConstraint(
            "(ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_by_verifier_id IS NOT NULL AND ended_reason IS NOT NULL AND "
            "ended_reason IN ('revoked', 'departed'))", name="ck_classroom_assignment_end_evidence",
        ),
        Index("uq_classroom_assignment_open", "class_id", "verifier_id", unique=True,
              sqlite_where=text("ended_at IS NULL"), postgresql_where=text("ended_at IS NULL")),
        Index("ix_classroom_assignment_program_verifier", "program_id", "verifier_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    verifier_id: Mapped[int] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_reason: Mapped[str | None] = mapped_column(String(10))
    granted_by_verifier_id: Mapped[int] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False,
    )
    ended_by_verifier_id: Mapped[int | None] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"),
    )


class ClassroomStudentMembership(Base):
    """Stable anchor only: no join API, entitlement, consent, or reporting grant."""

    __tablename__ = "classroom_student_memberships"
    __table_args__ = (
        UniqueConstraint("class_id", "profile_id", name="uq_classroom_membership_class_student"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classroom_classes.id", ondelete="RESTRICT"), nullable=False,
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False,
    )


class ClassroomMembershipPeriod(Base):
    """Future enrollment services must lock the anchor and reject all overlaps.

    A held segment is still a membership; a departed segment has ended. S1 only
    supplies the structure and never writes membership through a runtime service.
    """

    __tablename__ = "classroom_membership_periods"
    __table_args__ = (
        UniqueConstraint("membership_id", "starts_at", name="uq_classroom_membership_period_start"),
        CheckConstraint("state IN ('active', 'held')", name="ck_classroom_membership_period_state"),
        CheckConstraint("ended_at IS NULL OR ended_at > starts_at", name="ck_classroom_membership_period_dates"),
        CheckConstraint(
            "(ended_at IS NULL AND ended_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('departed', 'changed'))",
            name="ck_classroom_membership_period_end",
        ),
        Index("uq_classroom_membership_period_open", "membership_id", unique=True,
              sqlite_where=text("ended_at IS NULL"), postgresql_where=text("ended_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    membership_id: Mapped[int] = mapped_column(
        ForeignKey("classroom_student_memberships.id", ondelete="RESTRICT"), nullable=False,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    ended_reason: Mapped[str | None] = mapped_column(String(10))


class ClassroomOwnershipTransfer(Base):
    __tablename__ = "classroom_ownership_transfers"
    __table_args__ = (
        UniqueConstraint("id", "program_id", name="uq_classroom_transfer_id_program"),
        CheckConstraint("owner_version >= 1", name="ck_classroom_transfer_owner_version"),
        CheckConstraint("initiator_verifier_id <> recipient_verifier_id", name="ck_classroom_transfer_distinct_adults"),
        CheckConstraint("status IN ('pending', 'accepted', 'cancelled', 'superseded')", name="ck_classroom_transfer_status"),
        CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR "
            "(status <> 'pending' AND resolved_at IS NOT NULL AND resolved_at >= created_at)",
            name="ck_classroom_transfer_resolution",
        ),
        Index("uq_classroom_transfer_pending", "program_id", unique=True,
              sqlite_where=text("status = 'pending'"), postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("classroom_programs.organization_id", ondelete="RESTRICT"), nullable=False,
    )
    initiator_verifier_id: Mapped[int] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False,
    )
    recipient_verifier_id: Mapped[int] = mapped_column(
        ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False,
    )
    owner_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassroomAuditEvent(Base):
    """Transactional evidence, not an external restore/replay ledger."""

    __tablename__ = "classroom_audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["class_id", "program_id"], ["classroom_classes.id", "classroom_classes.program_id"],
            ondelete="RESTRICT", name="fk_classroom_audit_class_scope",
        ),
        ForeignKeyConstraint(
            ["role_grant_id", "program_id"], ["classroom_role_grants.id", "classroom_role_grants.program_id"],
            ondelete="RESTRICT", name="fk_classroom_audit_role_scope",
        ),
        ForeignKeyConstraint(
            ["assignment_id", "class_id", "program_id"],
            ["classroom_teaching_assignments.id", "classroom_teaching_assignments.class_id",
             "classroom_teaching_assignments.program_id"],
            ondelete="RESTRICT", name="fk_classroom_audit_assignment_scope",
        ),
        ForeignKeyConstraint(
            ["transfer_id", "program_id"],
            ["classroom_ownership_transfers.id", "classroom_ownership_transfers.program_id"],
            ondelete="RESTRICT", name="fk_classroom_audit_transfer_scope",
        ),
        CheckConstraint(
            "action IN ('program_provisioned', 'role_granted', 'role_revoked', 'class_created', "
            "'teacher_assigned', 'teacher_revoked', 'teacher_departed', 'transfer_proposed', "
            "'transfer_cancelled', 'transfer_superseded', 'ownership_transferred')",
            name="ck_classroom_audit_action",
        ),
        CheckConstraint(
            "action <> 'program_provisioned' OR "
            "(new_owner_id IS NOT NULL AND target_verifier_id IS NOT NULL)",
            name="ck_classroom_audit_provision_evidence",
        ),
        CheckConstraint(
            "action NOT IN ('role_granted', 'role_revoked') OR "
            "(role_grant_id IS NOT NULL AND target_verifier_id IS NOT NULL)",
            name="ck_classroom_audit_role_evidence",
        ),
        CheckConstraint(
            "action NOT IN ('teacher_assigned', 'teacher_revoked', 'teacher_departed') OR "
            "(assignment_id IS NOT NULL AND class_id IS NOT NULL AND target_verifier_id IS NOT NULL)",
            name="ck_classroom_audit_teacher_evidence",
        ),
        CheckConstraint("action <> 'class_created' OR class_id IS NOT NULL", name="ck_classroom_audit_class_evidence"),
        CheckConstraint(
            "action NOT IN ('transfer_proposed', 'transfer_cancelled', 'transfer_superseded', 'ownership_transferred') "
            "OR (transfer_id IS NOT NULL AND target_verifier_id IS NOT NULL)",
            name="ck_classroom_audit_transfer_evidence",
        ),
        CheckConstraint(
            "action <> 'ownership_transferred' OR "
            "(old_owner_id IS NOT NULL AND new_owner_id IS NOT NULL AND old_owner_id <> new_owner_id)",
            name="ck_classroom_audit_owner_evidence",
        ),
        Index("ix_classroom_audit_program_time", "program_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("classroom_programs.organization_id", ondelete="RESTRICT"), nullable=False,
    )
    class_id: Mapped[int | None] = mapped_column(Integer)
    actor_verifier_id: Mapped[int] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False)
    target_verifier_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    role_grant_id: Mapped[int | None] = mapped_column(Integer)
    assignment_id: Mapped[int | None] = mapped_column(Integer)
    transfer_id: Mapped[int | None] = mapped_column(Integer)
    old_owner_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"))
    new_owner_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False,
    )
