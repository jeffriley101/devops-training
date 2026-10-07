"""Explicit Classroom relationships; none of these records grants student access.

Historical Organization provenance and free verifier relationships remain separate.
Every authority record uses restrictive foreign keys so deleting an identity cannot
silently erase ownership, revocation, or audit evidence.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
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
    """Stable Class/student anchor; never entitlement, consent or reporting."""

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
    """Enrollment writers lock Program/Class/anchor before changing periods.

    c23 additionally enforces non-overlap through database triggers. Explicit
    holds use ClassroomMembershipHold; closing a period preserves its history.
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


class ClassroomEntitlement(Base):
    """Program-only access; independent of consumer billing and student benefits."""

    __tablename__ = "classroom_entitlements"
    __table_args__ = (
        UniqueConstraint("id", "program_id", name="uq_classroom_entitlement_scope"),
        CheckConstraint("source IN ('trial', 'institutional')", name="ck_classroom_entitlement_source"),
        CheckConstraint("status IN ('active', 'ended')", name="ck_classroom_entitlement_status"),
        CheckConstraint("ends_at > starts_at", name="ck_classroom_entitlement_dates"),
        CheckConstraint("class_limit >= 0 AND teacher_limit >= 0", name="ck_classroom_entitlement_limits"),
        CheckConstraint("length(trim(provenance)) BETWEEN 1 AND 100", name="ck_classroom_entitlement_provenance"),
        CheckConstraint(
            "(source = 'trial' AND approved_by_verifier_id IS NOT NULL AND approved_by_admin IS NULL) OR "
            "(source = 'institutional' AND approved_by_verifier_id IS NULL AND approved_by_admin IS NOT NULL)",
            name="ck_classroom_entitlement_approval",
        ),
        CheckConstraint(
            "(status = 'active' AND ended_at IS NULL) OR (status = 'ended' AND ended_at IS NOT NULL)",
            name="ck_classroom_entitlement_end",
        ),
        Index("uq_classroom_entitlement_trial", "program_id", unique=True,
              sqlite_where=text("source = 'trial'"), postgresql_where=text("source = 'trial'")),
        Index("ix_classroom_entitlement_program", "program_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("classroom_programs.organization_id", ondelete="RESTRICT"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    class_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    teacher_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_by_verifier_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"))
    approved_by_admin: Mapped[str | None] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassroomClassState(Base):
    """Absence is inactive: S1 relationships are never silently activated."""

    __tablename__ = "classroom_class_states"
    __table_args__ = (
        ForeignKeyConstraint(["class_id", "program_id"], ["classroom_classes.id", "classroom_classes.program_id"],
                             ondelete="RESTRICT", name="fk_classroom_state_scope"),
        CheckConstraint("state IN ('active', 'archived')", name="ck_classroom_class_state"),
        CheckConstraint("state <> 'archived' OR enrollment_open = false", name="ck_classroom_archived_closed"),
        Index("ix_classroom_state_program", "program_id"),
    )

    class_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    enrollment_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    changed_by_verifier_id: Mapped[int] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False)


class ClassroomEntryCode(Base):
    """Only keyed digests are retained; visible entry codes are issuance output."""

    __tablename__ = "classroom_entry_codes"
    __table_args__ = (
        UniqueConstraint("id", "class_id", "program_id", name="uq_classroom_code_scope"),
        UniqueConstraint("class_id", "generation", name="uq_classroom_code_generation"),
        ForeignKeyConstraint(["class_id", "program_id"], ["classroom_classes.id", "classroom_classes.program_id"],
                             ondelete="RESTRICT", name="fk_classroom_code_scope"),
        CheckConstraint("generation >= 1", name="ck_classroom_code_generation"),
        CheckConstraint("length(digest) = 64", name="ck_classroom_code_digest"),
        CheckConstraint(
            "(is_current = true AND revoked_at IS NULL AND revoked_by_verifier_id IS NULL) OR "
            "(is_current = false AND revoked_at IS NOT NULL AND revoked_by_verifier_id IS NOT NULL)",
            name="ck_classroom_code_revocation",
        ),
        Index("uq_classroom_code_current_class", "class_id", unique=True,
              sqlite_where=text("is_current = true"), postgresql_where=text("is_current = true")),
        Index("uq_classroom_code_current_digest", "program_id", "digest", unique=True,
              sqlite_where=text("is_current = true"), postgresql_where=text("is_current = true")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    issued_by_verifier_id: Mapped[int] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_verifier_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"))


class ClassroomMembershipHold(Base):
    """A released row is retained; the transactional audit retains every transition."""

    __tablename__ = "classroom_membership_holds"
    __table_args__ = (
        CheckConstraint("state IN ('suspended', 'removed')", name="ck_classroom_hold_state"),
        CheckConstraint("reason IN ('conduct', 'admin_removal')", name="ck_classroom_hold_reason"),
        CheckConstraint(
            "(released_at IS NULL AND released_by_verifier_id IS NULL) OR "
            "(released_at IS NOT NULL AND released_by_verifier_id IS NOT NULL AND released_at >= held_at)",
            name="ck_classroom_hold_release",
        ),
    )

    membership_id: Mapped[int] = mapped_column(ForeignKey("classroom_student_memberships.id", ondelete="RESTRICT"), primary_key=True)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    held_by_verifier_id: Mapped[int] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False)
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by_verifier_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"))


class ClassroomS2AuditEvent(Base):
    """Minimal S2 lifecycle evidence; never an entitlement or reporting grant."""

    __tablename__ = "classroom_s2_audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["class_id", "program_id"], ["classroom_classes.id", "classroom_classes.program_id"],
                             ondelete="RESTRICT", name="fk_classroom_s2_audit_class"),
        ForeignKeyConstraint(["entitlement_id", "program_id"], ["classroom_entitlements.id", "classroom_entitlements.program_id"],
                             ondelete="RESTRICT", name="fk_classroom_s2_audit_entitlement"),
        ForeignKeyConstraint(["code_id", "class_id", "program_id"],
                             ["classroom_entry_codes.id", "classroom_entry_codes.class_id", "classroom_entry_codes.program_id"],
                             ondelete="RESTRICT", name="fk_classroom_s2_audit_code"),
        CheckConstraint(
            "(CASE WHEN actor_verifier_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN actor_profile_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN actor_admin IS NOT NULL THEN 1 ELSE 0 END) = 1", name="ck_classroom_s2_audit_actor",
        ),
        CheckConstraint(
            "action IN ('trial_started', 'institutional_created', 'institutional_changed', 'institutional_ended', "
            "'class_activated', 'class_archived', 'class_reactivated', 'enrollment_opened', 'enrollment_closed', "
            "'code_issued', 'code_rotated', 'code_revoked', 'student_joined', 'student_left', 'student_suspended', "
            "'student_removed', 'student_reinstated', 'teacher_activated', 'teacher_deactivated')",
            name="ck_classroom_s2_audit_action",
        ),
        Index("ix_classroom_s2_audit_program_time", "program_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("classroom_programs.organization_id", ondelete="RESTRICT"), nullable=False)
    class_id: Mapped[int | None] = mapped_column(Integer)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT"))
    membership_id: Mapped[int | None] = mapped_column(ForeignKey("classroom_student_memberships.id", ondelete="RESTRICT"))
    actor_verifier_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"))
    actor_profile_id: Mapped[int | None] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT"))
    actor_admin: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    entitlement_id: Mapped[int | None] = mapped_column(Integer)
    code_id: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False)
