"""Enrollment V2 service foundation. No HTTP/UI, parent or reporting adapter.

A WC# identifies an existing account; only that account's current student
credentials can accept. Issuing, declining and deferring never enroll anyone.
Sharing-only invitations are a different table, purpose and service.
"""
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
import os

from sqlalchemy import select

from . import classroom as s1, classroom_s2 as s2
from .accounts import normalize_woodchuck_id
from .age_privacy import utc
from .classroom_enrollment_models import ClassroomEnrollmentAuditEvent as Audit, ClassroomEnrollmentInvitation as Invitation
from .classroom_models import ClassroomTeachingAssignment
from .models import WoodchuckProfile

PURPOSE = 'classroom-enrollment-v2'
LIFETIME = timedelta(days=7)
UNAVAILABLE = 'Classroom enrollment invitation unavailable.'


def enabled():
    return s2.enabled() and os.getenv('CLASSROOM_ENROLLMENT_V2_ENABLED') == '1'


def _operation(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        if not enabled():
            raise s1.ClassroomDenied(UNAVAILABLE)
        try:
            return function(session, *args, **kwargs)
        except s1.ClassroomDenied as error:
            raise s1.ClassroomDenied(UNAVAILABLE) from error
    return s2._mutation(checked)


def _ids(*values):
    if any(type(value) is not int or value <= 0 for value in values):
        raise s1.ClassroomDenied(UNAVAILABLE)


def _director(session, *, actor, program_id, class_id, assignment_id=None):
    """Ownership, administrative roles or another Class never imply teaching."""
    _ids(program_id, class_id)
    s1._lock_program(session, program_id)
    s2._active(session, program_id, class_id, enrollment=True)
    issuer = s1._actor(session, actor)
    query = select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.verifier_id == issuer,
        ClassroomTeachingAssignment.ended_at.is_(None))
    if assignment_id is not None:
        query = query.where(ClassroomTeachingAssignment.id == assignment_id)
    assignments = list(session.scalars(query.execution_options(populate_existing=True)))
    now = s2._now()
    if len(assignments) != 1 or utc(assignments[0].starts_at) > now:
        raise s1.ClassroomDenied(UNAVAILABLE)
    s2._paid(session, program_id)
    return assignments[0]


def _acceptance_authority(session, row):
    s2._active(session, row.program_id, row.class_id, enrollment=True)
    assignment = s1._fresh(session, ClassroomTeachingAssignment,
        ClassroomTeachingAssignment.id == row.assignment_id,
        ClassroomTeachingAssignment.program_id == row.program_id,
        ClassroomTeachingAssignment.class_id == row.class_id)
    now = s2._now()
    if (assignment is None or assignment.verifier_id != row.issued_by_verifier_id
            or assignment.ended_at is not None or utc(assignment.starts_at) > now):
        raise s1.ClassroomDenied(UNAVAILABLE)
    s2._paid(session, row.program_id)


def _locked(session, invitation_id):
    _ids(invitation_id)
    # Discover scope without locking an invitation ahead of its Program/Class.
    row = session.get(Invitation, invitation_id, populate_existing=True)
    if row is None or row.purpose != PURPOSE:
        raise s1.ClassroomDenied(UNAVAILABLE)
    s1._lock_program(session, row.program_id)
    s1._class(session, row.program_id, row.class_id)
    row = s1._fresh(session, Invitation, Invitation.id == invitation_id)
    if row is None or row.purpose != PURPOSE:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return row


def _audit(session, row, action, *, profile=None, verifier=None, period=None):
    session.add(Audit(invitation_id=row.id, program_id=row.program_id, class_id=row.class_id,
        profile_id=row.profile_id, action=action, actor_profile_id=profile, actor_verifier_id=verifier,
        membership_period_id=period, occurred_at=s2._now()))


def _resolve(session, row, state, *, profile=None, verifier=None, period=None):
    row.state, row.resolved_at, row.accepted_period_id = state, s2._now(), period
    _audit(session, row, state, profile=profile, verifier=verifier, period=period)


def status(row):
    """Clock expiry is effective even before a deliberate operation retains it."""
    if row.state in {'pending', 'deferred'} and s2._now() >= utc(row.expires_at):
        return 'expired'
    return row.state


@_operation
def issue_invitation(session, *, actor, program_id, class_id, woodchuck_id):
    assignment = _director(session, actor=actor, program_id=program_id, class_id=class_id)
    if not isinstance(woodchuck_id, str) or not 1 <= len(woodchuck_id) <= 64 or not woodchuck_id.isascii():
        raise s1.ClassroomDenied(UNAVAILABLE)
    target = session.scalar(select(WoodchuckProfile.id).where(
        WoodchuckProfile.woodchuck_id == normalize_woodchuck_id(woodchuck_id)))
    if target is None:
        raise s1.ClassroomDenied(UNAVAILABLE)
    # Enrollment eligibility is S2 account/parent authorization. Never reuse the
    # stronger S3 sharing/reporting age rule to exclude eligible child accounts.
    profile = s2._eligible_profile(session, target)
    assignment = _director(session, actor=actor, program_id=program_id, class_id=class_id,
                           assignment_id=assignment.id)
    anchor = s2._anchor(session, class_id, target)
    if anchor is not None:
        periods = s2._validated_periods(session, anchor.id)
        if s2._held(session, anchor.id) or any(p.ended_at is None and p.state == 'held' for p in periods):
            raise s1.ClassroomDenied(UNAVAILABLE)
    previous = s1._fresh(session, Invitation, Invitation.assignment_id == assignment.id,
        Invitation.profile_id == profile.id, Invitation.resolved_at.is_(None))
    now = s2._now()
    if previous is not None:
        if previous.purpose != PURPOSE or previous.state not in {'pending', 'deferred'} or utc(previous.issued_at) > now:
            raise s1.ClassroomDenied(UNAVAILABLE)
        if status(previous) != 'expired':
            return previous
        _resolve(session, previous, 'expired', verifier=assignment.verifier_id)
        session.flush()  # Release this assignment/student's open-offer index.
    row = Invitation(purpose=PURPOSE, program_id=program_id, class_id=class_id,
        assignment_id=assignment.id, profile_id=profile.id, issued_by_verifier_id=assignment.verifier_id,
        state='pending', issued_at=now, expires_at=now + LIFETIME)
    session.add(row)
    session.flush()
    _audit(session, row, 'issued', verifier=assignment.verifier_id)
    return row


@_operation
def student_invitation(session, *, student, invitation_id):
    row = _locked(session, invitation_id)
    if s2._student(session, student, require_eligibility=False).id != row.profile_id:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return row  # Internal object only; a future UI needs an explicit projection.


@dataclass(frozen=True)
class EnrollmentResponse:
    invitation_id: int
    state: str
    membership_id: int | None = None
    membership_period_id: int | None = None


def _accepted_membership(session, row):
    anchor = s2._anchor(session, row.class_id, row.profile_id)
    periods = s2._validated_periods(session, anchor.id) if anchor else []
    period = next((p for p in periods if p.id == row.accepted_period_id), None)
    if (anchor is None or s2._held(session, anchor.id) or period is None
            or row.resolved_at is None or utc(row.resolved_at) > s2._now()
            or utc(period.starts_at) > utc(row.resolved_at)
            or period.state != 'active' or period.ended_at is not None or utc(period.starts_at) > s2._now()):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return EnrollmentResponse(row.id, 'accepted', anchor.id, period.id)


@_operation
def respond(session, *, student, invitation_id, choice):
    if type(choice) is not str or choice not in {'accept', 'decline', 'defer'}:
        raise s1.ClassroomDenied(UNAVAILABLE)
    row = _locked(session, invitation_id)
    profile = s2._student(session, student, require_eligibility=choice == 'accept')
    if profile.id != row.profile_id or s2._now() < utc(row.issued_at):
        raise s1.ClassroomDenied(UNAVAILABLE)
    if row.state == 'accepted':
        if choice != 'accept':
            raise s1.ClassroomDenied(UNAVAILABLE)  # Leaving is a separate S2 action.
        _acceptance_authority(session, row)
        return _accepted_membership(session, row)
    if row.state not in {'pending', 'deferred'}:
        return EnrollmentResponse(row.id, row.state)
    if status(row) == 'expired':
        _resolve(session, row, 'expired', profile=profile.id)
    elif choice == 'accept':
        # Account locks can wait: recheck assignment, enrollment, capacity and
        # expiry after the wait, immediately before the shared membership write.
        _acceptance_authority(session, row)
        if s2._now() >= utc(row.expires_at):
            _resolve(session, row, 'expired', profile=profile.id)
        else:
            anchor = s2._enroll_student(session, profile=profile, program_id=row.program_id, class_id=row.class_id)
            period = next(p for p in s2._validated_periods(session, anchor.id) if p.ended_at is None)
            # Retain both transitions in the same savepoint/outer transaction.
            _resolve(session, row, 'accepted', profile=profile.id, period=period.id)
            return EnrollmentResponse(row.id, 'accepted', anchor.id, period.id)
    elif choice == 'decline':
        _resolve(session, row, 'declined', profile=profile.id)
    elif row.state != 'deferred':
        row.state = 'deferred'
        _audit(session, row, 'deferred', profile=profile.id)
    return EnrollmentResponse(row.id, row.state)


@_operation
def revoke_invitation(session, *, actor, invitation_id):
    row = _locked(session, invitation_id)
    issuer = s1._actor(session, actor)
    if issuer != row.issued_by_verifier_id or row.state == 'accepted':
        raise s1.ClassroomDenied(UNAVAILABLE)
    if row.state in {'pending', 'deferred'}:
        if s2._now() < utc(row.issued_at):
            raise s1.ClassroomDenied(UNAVAILABLE)
        _resolve(session, row, 'expired' if status(row) == 'expired' else 'revoked', verifier=issuer)
    return row
