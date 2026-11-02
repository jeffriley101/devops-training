"""One verified school approver and agreement per Program/July-June year.

These receipts never create roles, enrollment, consent or reporting permissions.
The draft legal contract has no approved live version. Recording the workflow
is distinct from treating it as effective school authorization.
"""
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
import re
from urllib.parse import urlsplit

from sqlalchemy import or_, select

from . import classroom as s1, classroom_s2 as s2
from .age_privacy import utc
from .classroom_models import ClassroomRoleGrant, ClassroomTeachingAssignment
from .classroom_school_models import ClassroomSchoolAuthorization as Authorization
from .models import Organization, TrustedVerifier
from .student_practice_metrics import CENTRAL
from .verifiers import validate_email

UNAVAILABLE = 'School authorization unavailable.'
AGREEMENT_VERSION = 'classroom-school-agreement-draft-v1'
AGREEMENT = (
    'I approve Classroom use for the named Music Program and school for the stated July 1-June 30 '
    'authorization year, within the school-granted authority independently verified below. '
    'Students must choose to Join and Share. Required parent or legal-guardian authorization '
    'remains separate. Individual P-Charts require their own student sharing choice and applicable '
    'consent. Comprehensive director reporting is unavailable for under-13 students. This agreement '
    'does not expose earlier practice history, enroll students, authorize additional data fields, '
    'grant director roles or replace student/parent consent. Authorization expires at the end of '
    'the stated year and does not renew automatically. It can be withdrawn or revoked. '
    'This draft remains subject to legal review before it can authorize live school use.'
)
AGREEMENT_DIGEST = hashlib.sha256((AGREEMENT_VERSION + '\n' + AGREEMENT).encode()).hexdigest()
# No environment variable can turn a legally unapproved draft into an approved
# agreement. An explicitly reviewed version must be added after legal acceptance.
LEGALLY_APPROVED_AGREEMENTS = frozenset()


def enabled():
    return s2.enabled() and os.getenv('CLASSROOM_SCHOOL_AUTHORIZATION_ENABLED') == '1'


def _operation(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        if not enabled():
            raise s1.ClassroomDenied(UNAVAILABLE)
        return function(session, *args, **kwargs)
    return s1._mutation(checked)


def school_year(at):
    local = utc(at).astimezone(CENTRAL)
    return local.year if local.month >= 7 else local.year - 1


def year_bounds(year):
    if type(year) is not int or not 2000 <= year <= 9998:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return tuple(datetime(y, 7, 1, tzinfo=CENTRAL).astimezone(timezone.utc) for y in (year, year + 1))


def _text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum
            or re.search(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]', value)):
        raise s1.ClassroomDenied('Complete the school authority evidence without control characters or student information.')
    return value.strip()


def _evidence_digest(row):
    values = [getattr(row, key) for key in ('program_id', 'approver_id', 'authority_kind', 'school_name',
        'authority_description', 'contact_name', 'contact_role', 'contact_address', 'contact_source',
        'verification_method', 'verification_notes', 'verified_by_id', 'verified_admin_fingerprint', 'year_start')]
    values += [utc(row.contacted_at).isoformat(), utc(row.verified_at).isoformat(), utc(row.expires_at).isoformat()]
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


def _latest(session, program_id, year):
    return session.scalar(select(Authorization).where(Authorization.program_id == program_id,
        Authorization.year_start == year).order_by(Authorization.id.desc()).limit(1)
        .execution_options(populate_existing=True))


def revision(row):
    return (row.id, utc(row.approved_at).isoformat() if row.approved_at else None,
        utc(row.revoked_at).isoformat() if row.revoked_at else None, row.evidence_digest) if row else (None, None, None, None)


def _record(session, program_id, record_id):
    if type(record_id) is not int or record_id <= 0:
        raise s1.ClassroomDenied(UNAVAILABLE)
    row = s1._fresh(session, Authorization, Authorization.id == record_id, Authorization.program_id == program_id)
    if row is None:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return row


def _evidence_current(row, now):
    try:
        _, end = year_bounds(row.year_start)
        return (row.revoked_at is None and utc(row.expires_at) == end
            and utc(row.contacted_at) <= utc(row.verified_at) <= now < end
            and row.approver_id != row.verified_by_id and row.evidence_digest == _evidence_digest(row)
            and row.agreement_version == AGREEMENT_VERSION and row.agreement_digest == AGREEMENT_DIGEST)
    except (ValueError, TypeError, AttributeError):
        return False


@_operation
def verify_authority(session, *, admin, reviewer, program_id, approver_id, authority_kind, school_name,
        authority_description, contact_name, contact_role, contact_address, contact_source,
        verification_method, verification_notes, contacted_at, year_start, independently_confirmed, previous):
    s1._lock_program(session, program_id)
    fingerprint = s2._admin(admin)
    reviewer_id = s1._actor(session, reviewer)
    s1._require_adults(session, approver_id)
    now = s2._now()
    if (reviewer_id == approver_id or independently_confirmed is not True
            or authority_kind not in {'director', 'administrator'}
            or verification_method not in {'phone', 'official_email'}
            or type(year_start) is not int or year_start not in {school_year(now), school_year(now) + 1}
            or not isinstance(contacted_at, datetime) or contacted_at.tzinfo is None
            or utc(contacted_at) > now):
        raise s1.ClassroomDenied(UNAVAILABLE)
    old = _latest(session, program_id, year_start)
    if previous != revision(old) or old and old.revoked_at is None:
        raise s1.ClassroomDenied('Revoke the existing year record before replacing its verified authority.')
    values = {name: _text(value, maximum) for name, value, maximum in (
        ('school_name', school_name, 150), ('authority_description', authority_description, 500),
        ('contact_name', contact_name, 100), ('contact_role', contact_role, 100),
        ('contact_address', contact_address, 254), ('contact_source', contact_source, 500),
        ('verification_notes', verification_notes, 1000))}
    source = urlsplit(values['contact_source'])
    approver = session.get(TrustedVerifier, approver_id, populate_existing=True)
    if (source.scheme != 'https' or not source.hostname or source.username or source.password
            or values['contact_name'].casefold() == approver.display_name.strip().casefold()
            or values['contact_address'].casefold() == approver.email.casefold()):
        raise s1.ClassroomDenied('An independently sourced, different official school contact is required.')
    if verification_method == 'phone':
        if (not re.fullmatch(r'[+() .0-9-]{7,40}', values['contact_address'])
                or sum(character.isdigit() for character in values['contact_address']) < 7):
            raise s1.ClassroomDenied('Record the official school phone number and call evidence.')
    else:
        try:
            values['contact_address'] = validate_email(values['contact_address'])
        except ValueError as error:
            raise s1.ClassroomDenied(UNAVAILABLE) from error
    row = Authorization(program_id=program_id, approver_id=approver_id, authority_kind=authority_kind,
        **values, verification_method=verification_method, contacted_at=utc(contacted_at), verified_at=now,
        verified_by_id=reviewer_id, verified_admin_fingerprint=fingerprint, year_start=year_start,
        expires_at=year_bounds(year_start)[1], agreement_version=AGREEMENT_VERSION,
        agreement_digest=AGREEMENT_DIGEST, evidence_digest='')
    row.evidence_digest = _evidence_digest(row)
    session.add(row); session.flush()
    s2._admin(admin); s1._actor(session, reviewer)
    return row


@_operation
def approve(session, *, actor, program_id, record_id, previous, agreement_version, agreement_digest, explicit):
    s1._lock_program(session, program_id)
    row = _record(session, program_id, record_id)
    now = s2._now()
    if (s1._actor(session, actor) != row.approver_id or explicit is not True
            or previous != revision(row) or agreement_version != AGREEMENT_VERSION
            or agreement_digest != AGREEMENT_DIGEST or not _evidence_current(row, now)
            or _latest(session, program_id, row.year_start).id != row.id):
        raise s1.ClassroomDenied(UNAVAILABLE)
    if row.approved_at is None:
        row.approved_at = now
        session.flush()
    return row


@_operation
def revoke(session, *, actor, program_id, record_id, previous, reason, admin=None):
    program = s1._lock_program(session, program_id)
    row = _record(session, program_id, record_id)
    actor_id = s1._actor(session, actor)
    if admin is not None:
        s2._admin(admin)
    elif actor_id != row.approver_id:
        s1._authority(session, program, actor, admin=True)
    if previous != revision(row) or reason not in {'withdrawn', 'authority_revoked', 'replaced'}:
        raise s1.ClassroomDenied(UNAVAILABLE)
    if row.revoked_at is None:
        row.revoked_at, row.revoked_by_id, row.revocation_reason = max(s2._now(), utc(row.verified_at)), actor_id, reason
        session.flush()
    return row


def _viewer(session, program, actor_id):
    now = s2._now()
    return bool(program.owner_verifier_id == actor_id
        or session.scalar(select(ClassroomRoleGrant.id).where(ClassroomRoleGrant.program_id == program.organization_id,
            ClassroomRoleGrant.verifier_id == actor_id, ClassroomRoleGrant.starts_at <= now,
            ClassroomRoleGrant.ended_at.is_(None)).limit(1))
        or session.scalar(select(ClassroomTeachingAssignment.id).where(
            ClassroomTeachingAssignment.program_id == program.organization_id,
            ClassroomTeachingAssignment.verifier_id == actor_id, ClassroomTeachingAssignment.starts_at <= now,
            ClassroomTeachingAssignment.ended_at.is_(None)).limit(1)))


def status(row, now):
    if row.revoked_at is not None:
        return 'revoked'
    if utc(row.expires_at) <= now:
        return 'expired'
    if not _evidence_current(row, now):
        return 'evidence_unavailable'
    if row.approved_at is None:
        return 'awaiting_approver'
    if utc(row.approved_at) > now:
        return 'evidence_unavailable'
    if (row.agreement_version, row.agreement_digest) not in LEGALLY_APPROVED_AGREEMENTS:
        return 'legal_review_pending'
    if now < year_bounds(row.year_start)[0]:
        return 'scheduled'
    return 'authorized'


@_operation
def view(session, *, actor, program_id, admin=None):
    program = s1._lock_program(session, program_id)
    actor_id = s1._actor(session, actor)
    staff = admin is not None
    if staff:
        s2._admin(admin)
    manager = _viewer(session, program, actor_id)
    may_revoke = program.owner_verifier_id == actor_id or 'admin' in s1._roles(session, program_id, actor_id)
    query = select(Authorization).where(Authorization.program_id == program_id)
    if not staff and not manager:
        query = query.where(Authorization.approver_id == actor_id)
    records = session.scalars(query.order_by(Authorization.id.desc()).limit(20)
        .execution_options(populate_existing=True)).all()
    if not staff and not manager and not records:
        raise s1.ClassroomDenied(UNAVAILABLE)
    now = s2._now()
    rows = []
    for row in records:
        item = {key: getattr(row, key) for key in ('id', 'year_start', 'school_name', 'authority_kind',
            'authority_description', 'approved_at', 'verified_at', 'expires_at', 'revoked_at', 'revocation_reason')}
        item.update(status=status(row, now), approver=session.get(TrustedVerifier, row.approver_id).display_name,
            revision=revision(row), can_approve=row.approver_id == actor_id and row.approved_at is None
                and _evidence_current(row, now), can_revoke=(staff or may_revoke or row.approver_id == actor_id)
                and row.revoked_at is None)
        if staff:
            item['evidence'] = {key: getattr(row, key) for key in ('contact_name', 'contact_role', 'contact_address',
                'contact_source', 'verification_method', 'verification_notes', 'contacted_at', 'evidence_digest')}
            item['reviewer'] = session.get(TrustedVerifier, row.verified_by_id).display_name
        rows.append(item)
    return {'program_id': program_id, 'program_name': session.get(Organization, program_id).name,
        'records': rows, 'years': (school_year(now), school_year(now) + 1),
        'revisions': {year: revision(_latest(session, program_id, year)) for year in (school_year(now), school_year(now)+1)}}


@_operation
def authorized_program(session, *, program_id):
    """Organizational status only; never a student-data authorization token."""
    s1._lock_program(session, program_id)
    row = _latest(session, program_id, school_year(s2._now()))
    return bool(row and status(row, s2._now()) == 'authorized')
