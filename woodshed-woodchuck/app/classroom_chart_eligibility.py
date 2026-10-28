"""Internal eligibility evidence for an enrolled student's P-Chart recipient.

This is not a sharing grant or a report read. Callers recheck it inside their
transaction. Invitations remain distinct from confirmed enrollment.
"""
from dataclasses import dataclass

from sqlalchemy import select

from . import classroom, classroom_director, classroom_reporting, classroom_s2
from .age_privacy import utc
from .classroom_models import (ClassroomTeachingAssignment, ClassroomStudentInvitation,
                              ClassroomEntitlement, ClassroomS2AuditEvent)


@dataclass(frozen=True)
class EnrolledRecipient:
    program_id: int
    class_id: int
    profile_id: int
    membership_period_id: int | None
    assignment_id: int
    verifier_id: int
    entitlement_id: int
    invitation_id: int | None = None


def enrolled_recipient(session, *, student, program_id, class_id, assignment_id):
    """Check one exact current teaching assignment using live student proof.

    The evidence is for internal eligibility only; consumers must recheck it
    within the transaction that creates or accesses a specific sharing grant.
    No generic join code, verifier connection, or boundary grant is accepted.
    """
    if not classroom_director.enabled() or type(assignment_id) is not int or assignment_id <= 0:
        raise classroom.ClassroomDenied("Classroom sharing unavailable.")
    if type(student) is not classroom_s2.StudentAuthentication:
        raise classroom.ClassroomDenied("Classroom sharing unavailable.")
    try:
        _, member, entitlement, _, _, now = classroom_reporting._context(
            session, program_id, class_id, student.profile_id, student=student)
        assignment = session.scalar(select(ClassroomTeachingAssignment).where(
            ClassroomTeachingAssignment.id == assignment_id,
            ClassroomTeachingAssignment.program_id == program_id,
            ClassroomTeachingAssignment.class_id == class_id,
            ClassroomTeachingAssignment.starts_at <= now,
            ClassroomTeachingAssignment.ended_at.is_(None),
        ).execution_options(populate_existing=True))
        if assignment is None:
            raise classroom.ClassroomDenied("Classroom sharing unavailable.")
        return EnrolledRecipient(program_id, class_id, student.profile_id, member.id,
                                 assignment.id, assignment.verifier_id, entitlement.id)
    except classroom.ClassroomDenied as error:
        raise classroom.ClassroomDenied("Classroom sharing unavailable.") from error


def invitation_matches(row, assignment, profile_id, now):
    """Exact accepted evidence; neither a generic link nor another teacher's invite."""
    from .classroom_student_invitations import accepted_current
    return (row is not None and row.profile_id == profile_id
        and row.accepted_by_profile_id == profile_id
        and row.assignment_id == assignment.id and row.class_id == assignment.class_id
        and row.program_id == assignment.program_id and row.issued_by_verifier_id == assignment.verifier_id
        and accepted_current(row, now))


def invited_context(session, *, assignment_id, profile_id, student=None, invitation_id=None):
    """Current invitation evidence under the same Program/account lock order.

    A supplied invitation ID is an exact historical source, never replaceable
    by a newer invitation or by later enrollment.
    """
    from . import classroom_student_invitations as invitations
    reporting, s2 = classroom_reporting, classroom_s2
    reporting._exact_ids(assignment_id, profile_id)
    assignment = session.get(ClassroomTeachingAssignment, assignment_id, populate_existing=True)
    if assignment is None:
        raise classroom.ClassroomDenied("Classroom sharing unavailable.")
    classroom._lock_program(session, assignment.program_id)
    state = s2._active(session, assignment.program_id, assignment.class_id)
    if student is None:
        s2._eligible_profile(session, profile_id)
    elif s2._student(session, student).id != profile_id:
        raise classroom.ClassroomDenied("Classroom sharing unavailable.")
    rule = reporting._account_rule(session, profile_id)
    anchor = s2._anchor(session, assignment.class_id, profile_id)
    if anchor is not None:
        s2._validated_periods(session, anchor.id)
        if s2._held(session, anchor.id):
            raise classroom.ClassroomDenied("Classroom sharing unavailable.")
    query = select(ClassroomStudentInvitation).where(
        ClassroomStudentInvitation.assignment_id == assignment_id,
        ClassroomStudentInvitation.profile_id == profile_id)
    if invitation_id is not None:
        query = query.where(ClassroomStudentInvitation.id == invitation_id)
    rows = session.scalars(query.order_by(ClassroomStudentInvitation.id.desc())
        .with_for_update().execution_options(populate_existing=True)).all()
    # Recheck authority and time after all blocking locks, including invitation.
    now = invitations._assignment(session, assignment.program_id, assignment.class_id,
                                  assignment.id, assignment.verifier_id)
    row = next((r for r in rows if invitation_matches(r, assignment, profile_id, now)), None)
    entitlement = s2.entitlement_decision(session, assignment.program_id, at=now)
    activation = reporting._activation(session, state)
    if row is None or not entitlement.allowed or activation > now or utc(rule.declared_at) > now:
        raise classroom.ClassroomDenied("Classroom sharing unavailable.")
    entitlement = session.get(ClassroomEntitlement, entitlement.entitlement_id, populate_existing=True)
    return (anchor, None, entitlement, rule, activation, now), row


def invitation_source_current(session, share, invitation, now):
    """Retained interruptions must not reopen old chart permissions."""
    if utc(invitation.accepted_at) > utc(share.selected_at):
        return False
    watermark = (session.get(ClassroomS2AuditEvent, share.source_audit_id, populate_existing=True)
                 if share.source_audit_id is not None else None)
    entitlement = session.get(ClassroomEntitlement, share.entitlement_id, populate_existing=True)
    if (share.source_audit_id is not None and (watermark is None
            or watermark.program_id != invitation.program_id
            or utc(watermark.occurred_at) > utc(share.selected_at))):
        return False
    if (entitlement is None or entitlement.program_id != invitation.program_id
            or utc(entitlement.ends_at) <= now
            or entitlement.ended_at is not None and utc(entitlement.ended_at) <= now):
        return False
    interruption = session.scalar(select(ClassroomS2AuditEvent.id).where(
        ClassroomS2AuditEvent.program_id == invitation.program_id,
        ClassroomS2AuditEvent.id > (share.source_audit_id or 0),
        ClassroomS2AuditEvent.occurred_at <= now,
        ((ClassroomS2AuditEvent.class_id == invitation.class_id)
         & ((ClassroomS2AuditEvent.action == "class_archived")
            | ((ClassroomS2AuditEvent.profile_id == invitation.profile_id)
               & ClassroomS2AuditEvent.action.in_(("student_left", "student_removed", "student_suspended")))))
        | ClassroomS2AuditEvent.action.in_(("institutional_created", "institutional_changed", "institutional_ended")))
        .limit(1))
    return interruption is None
