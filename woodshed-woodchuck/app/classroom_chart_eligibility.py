"""Internal eligibility evidence for an enrolled student's P-Chart recipient.

This is not a sharing grant or a report read. Callers recheck it inside their
transaction. Student-specific director invitations are not implemented by S2.
"""
from dataclasses import dataclass

from sqlalchemy import select

from . import classroom, classroom_director, classroom_reporting, classroom_s2
from .classroom_models import ClassroomTeachingAssignment


@dataclass(frozen=True)
class EnrolledRecipient:
    program_id: int
    class_id: int
    profile_id: int
    membership_period_id: int
    assignment_id: int
    verifier_id: int
    entitlement_id: int


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
