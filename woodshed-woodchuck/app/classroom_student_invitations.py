"""Student-specific invitation evidence, separate from enrollment and reporting.

Acceptance records existing-account authentication, not parental consent or
reporting authorization. Individual chart sharing independently requires an
explicit student choice and current invitation, assignment and entitlement.
"""
from functools import wraps
import base64
import hashlib
import hmac
import json
import re

from sqlalchemy import select

from . import classroom as s1, classroom_director, classroom_reporting as reporting, classroom_s2 as s2
from .age_privacy import utc
from .classroom_invitation import LIFETIME
from .classroom_models import ClassroomStudentInvitation, ClassroomTeachingAssignment

UNAVAILABLE = "Classroom invitation unavailable."
IDENTITY_PURPOSE = "classroom-student-invitation-identity:v1"


def _mutation(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        if not classroom_director.enabled():
            raise s1.ClassroomDenied(UNAVAILABLE)
        try:
            return function(session, *args, **kwargs)
        except s1.ClassroomDenied as error:
            raise s1.ClassroomDenied(UNAVAILABLE) from error
    return s1._mutation(checked)


def _assignment(session, program_id, class_id, assignment_id, issuer_id):
    s2._active(session, program_id, class_id)
    row = s1._fresh(session, ClassroomTeachingAssignment,
        ClassroomTeachingAssignment.id == assignment_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.program_id == program_id)
    now = s2._now()
    if (row is None or row.verifier_id != issuer_id or row.ended_at is not None
            or utc(row.starts_at) > now
            or not s2.entitlement_decision(session, program_id, at=now).allowed):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return now


def _locked(session, invitation_id):
    reporting._exact_ids(invitation_id)
    row = session.get(ClassroomStudentInvitation, invitation_id, populate_existing=True)
    if row is None:
        raise s1.ClassroomDenied(UNAVAILABLE)
    s1._lock_program(session, row.program_id)
    return s1._fresh(session, ClassroomStudentInvitation, ClassroomStudentInvitation.id == invitation_id)


@_mutation
def issue(session, *, actor, program_id, class_id, assignment_id, profile_id):
    """Record an exact teacher's invitation to an existing eligible account."""
    reporting._exact_ids(program_id, class_id, assignment_id, profile_id)
    s1._lock_program(session, program_id)
    issuer_id = s1._actor(session, actor)
    _assignment(session, program_id, class_id, assignment_id, issuer_id)
    s2._eligible_profile(session, profile_id)
    reporting._account_rule(session, profile_id)
    # Account locks may wait: recheck current authority/time after the wait.
    now = _assignment(session, program_id, class_id, assignment_id, s1._actor(session, actor))
    row = ClassroomStudentInvitation(program_id=program_id, class_id=class_id,
        assignment_id=assignment_id, profile_id=profile_id, issued_by_verifier_id=issuer_id,
        issued_at=now, expires_at=now + LIFETIME)
    session.add(row)
    session.flush()
    return row


@_mutation
def accept(session, *, student, invitation_id):
    """Bind acceptance to live, transaction-scoped student credential evidence."""
    row = _locked(session, invitation_id)
    profile = s2._student(session, student)
    if profile.id != row.profile_id:
        raise s1.ClassroomDenied(UNAVAILABLE)
    reporting._account_rule(session, profile.id)
    now = _assignment(session, row.program_id, row.class_id, row.assignment_id, row.issued_by_verifier_id)
    if (row.revoked_at is not None or row.accepted_at is not None
            or not utc(row.issued_at) <= now < utc(row.expires_at)):
        raise s1.ClassroomDenied(UNAVAILABLE)
    row.accepted_at, row.accepted_by_profile_id = now, profile.id
    row.deadline_accepted_at = now
    return row


@_mutation
def revoke(session, *, actor, invitation_id):
    """The issuer can revoke retained evidence even after assignment/plan expiry."""
    row = _locked(session, invitation_id)
    if s1._actor(session, actor) != row.issued_by_verifier_id:
        raise s1.ClassroomDenied(UNAVAILABLE)
    if row.revoked_at is None:
        row.revoked_at = max(s2._now(), utc(row.accepted_at or row.issued_at))
        row.revoked_by_verifier_id = row.issued_by_verifier_id
    return row


def _identity_binding(profile):
    return hmac.new(s2._key(IDENTITY_PURPOSE + ":account"),
        f"{profile.id}:{profile.session_version}:{profile.pin_hash}".encode(), hashlib.sha256).hexdigest()


def accepted_current(row, now):
    """Keep legacy expiry unless this acceptance used the new deadline policy.

    Never promote an already accepted legacy row, even if it is still live.
    This also covers expiry between schema installation and application rollout.
    """
    if (row.accepted_at is None or row.accepted_by_profile_id != row.profile_id
            or row.revoked_at is not None
            or not utc(row.issued_at) <= utc(row.accepted_at) < utc(row.expires_at)
            or utc(row.accepted_at) > now):
        return False
    if row.deadline_accepted_at is None:
        return now < utc(row.expires_at)
    return utc(row.deadline_accepted_at) == utc(row.accepted_at)


def _parse_identity(token):
    # Authenticate the purpose and payload before querying any student identity.
    try:
        if not isinstance(token, str) or len(token) > 1500 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[0-9a-f]{64}", token):
            raise ValueError()
        body, signature = token.split('.')
        expected = hmac.new(s2._key(IDENTITY_PURPOSE), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if (not isinstance(data, dict) or set(data) != {'student', 'binding', 'iat', 'exp'}
                or s2._encode(data) != body or any(type(data[k]) is not int for k in ('student', 'iat', 'exp'))
                or data['student'] <= 0 or not isinstance(data['binding'], str)
                or data['exp'] - data['iat'] != int(s2.INTENT_LENGTH.total_seconds())
                or not data['iat'] <= int(s2._now().timestamp()) < data['exp']):
            raise ValueError()
        return data
    except (ValueError, TypeError, UnicodeError) as error:
        raise s1.ClassroomDenied(UNAVAILABLE) from error


@_mutation
def prepare_identity(session, *, student):
    """A short-lived code the authenticated student deliberately gives a director."""
    profile = s2._student(session, student)
    reporting._account_rule(session, profile.id)
    now = int(s2._now().timestamp())
    body = s2._encode({'student': profile.id, 'binding': _identity_binding(profile),
        'iat': now, 'exp': now + int(s2.INTENT_LENGTH.total_seconds())})
    return body + '.' + hmac.new(s2._key(IDENTITY_PURPOSE), body.encode(), hashlib.sha256).hexdigest()


@_mutation
def director_assignment(session, *, actor, program_id, class_id):
    reporting._exact_ids(program_id, class_id)
    s1._lock_program(session, program_id)
    issuer = s1._actor(session, actor)
    assignments = list(session.scalars(select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.verifier_id == issuer,
        ClassroomTeachingAssignment.ended_at.is_(None))))
    if len(assignments) != 1:
        raise s1.ClassroomDenied(UNAVAILABLE)
    row = assignments[0]
    _assignment(session, program_id, class_id, row.id, s1._actor(session, actor))
    return row


@_mutation
def issue_for_identity(session, *, actor, program_id, class_id, identity):
    assignment = director_assignment(session, actor=actor, program_id=program_id, class_id=class_id)
    data = _parse_identity(identity)
    profile = s2._eligible_profile(session, data['student'])
    reporting._account_rule(session, profile.id)
    _parse_identity(identity)  # Expiration may pass while waiting for account locks.
    if not hmac.compare_digest(data['binding'], _identity_binding(profile)):
        raise s1.ClassroomDenied(UNAVAILABLE)
    existing = session.scalars(select(ClassroomStudentInvitation).where(
            ClassroomStudentInvitation.assignment_id == assignment.id,
            ClassroomStudentInvitation.profile_id == profile.id,
            ClassroomStudentInvitation.revoked_at.is_(None))).all()
    now = s2._now()
    if any(accepted_current(row, now) or (row.accepted_at is None and now < utc(row.expires_at))
           for row in existing):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return issue(session, actor=actor, program_id=program_id, class_id=class_id,
                 assignment_id=assignment.id, profile_id=profile.id)


@_mutation
def student_invitation(session, *, student, invitation_id):
    """Read only the intended student's invitation, never their practice records."""
    row = _locked(session, invitation_id)
    profile = s2._student(session, student)
    if row.profile_id != profile.id:
        raise s1.ClassroomDenied(UNAVAILABLE)
    reporting._account_rule(session, profile.id)
    _assignment(session, row.program_id, row.class_id, row.assignment_id, row.issued_by_verifier_id)
    return row
