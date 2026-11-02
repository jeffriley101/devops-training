"""Private Program school identity. WC# selects an account; adult proof authorizes.

Only the current name is retained. Audit records contain scoped references and
versions, never former names, name hashes, student mailboxes or public nicknames.
"""
import os
import unicodedata

from sqlalchemy import select, func

from . import classroom as s1, classroom_s2 as s2, classroom_enrollment
from .accounts import normalize_woodchuck_id
from .age_privacy import sharing_allowed
from .classroom_identity_models import ClassroomRosterIdentity as Identity, ClassroomRosterIdentityAudit as Audit
from .classroom_models import ClassroomStudentMembership
from .models import WoodchuckProfile

UNAVAILABLE = 'Private Class identity unavailable. Reopen the roster and check current authority.'


def enabled():
    return (classroom_enrollment.enabled() and os.getenv('CLASSROOM_DIRECTOR_ENABLED') == '1'
        and os.getenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED') == '1')


def normalize_name(value):
    if not isinstance(value, str) or len(value) > 300:
        raise s1.ClassroomDenied(UNAVAILABLE)
    value = unicodedata.normalize('NFC', value).strip()
    if not 1 <= len(value) <= 150 or any(unicodedata.category(char)[0] == 'C' or char in '\u2028\u2029' for char in value):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return value


def _validated(session, row):
    if row is None:
        return None
    count, first, last = session.execute(select(func.count(Audit.id), func.min(Audit.version), func.max(Audit.version)).where(
        Audit.identity_id == row.id, Audit.program_id == row.program_id, Audit.profile_id == row.profile_id)).one()
    if count != row.version or first != 1 or last != row.version:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return row


def _roster_identity(session, *, program_id, profile_id):
    """Internal projection AFTER management roster has authorized this exact row.

    Not a directory or a public/name lookup API. Disabled gate queries no tables.
    """
    if not enabled():
        return {'school_name': None, 'identity_version': 0}
    if session.info.get('classroom_write_program') != (session.get_transaction(), program_id):
        raise s1.ClassroomDenied(UNAVAILABLE)
    row = _validated(session, session.scalar(select(Identity).where(
        Identity.program_id == program_id, Identity.profile_id == profile_id).execution_options(populate_existing=True)))
    return {'school_name': row.school_name if row else None, 'identity_version': row.version if row else 0}


def _subject(session, *, actor, program_id, class_id, woodchuck_id):
    from .classroom_management_views import scope
    if not enabled():
        raise s1.ClassroomDenied(UNAVAILABLE)
    current = scope(session, actor=actor, program_id=program_id, class_id=class_id)
    s2._paid(session, program_id)
    s2._active(session, program_id, class_id)
    if not isinstance(woodchuck_id, str) or len(woodchuck_id) > 64 or not woodchuck_id.isascii():
        raise s1.ClassroomDenied(UNAVAILABLE)
    try:
        wc = normalize_woodchuck_id(woodchuck_id)
    except ValueError as error:
        raise s1.ClassroomDenied(UNAVAILABLE) from error
    profile_id = session.scalar(select(WoodchuckProfile.id).join(ClassroomStudentMembership,
        ClassroomStudentMembership.profile_id == WoodchuckProfile.id).where(
        ClassroomStudentMembership.class_id == class_id, WoodchuckProfile.woodchuck_id == wc))
    if profile_id is None:
        raise s1.ClassroomDenied(UNAVAILABLE)
    # Follow the accepted Program -> consent -> profile order, and recheck all
    # authority after account locks may have waited.
    profile = s2._eligible_profile(session, profile_id)
    scope(session, actor=actor, program_id=program_id, class_id=class_id)
    s2._paid(session, program_id)
    s2._active(session, program_id, class_id)
    anchor = s1._fresh(session, ClassroomStudentMembership,
        ClassroomStudentMembership.class_id == class_id, ClassroomStudentMembership.profile_id == profile.id)
    periods = s2._validated_periods(session, anchor.id)
    if (not sharing_allowed(session, profile.id) or s2._held(session, anchor.id)
            or not any(p.state == 'active' and p.ended_at is None and s2.utc(p.starts_at) <= s2._now() for p in periods)):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return current['actor_id'], profile


@s2._mutation
def set_school_name(session, *, actor, program_id, class_id, woodchuck_id, school_name, expected_version):
    if type(expected_version) is not int or expected_version < 0:
        raise s1.ClassroomDenied(UNAVAILABLE)
    actor_id, profile = _subject(session, actor=actor, program_id=program_id,
        class_id=class_id, woodchuck_id=woodchuck_id)
    name = normalize_name(school_name)
    row = _validated(session, session.scalar(select(Identity).where(
        Identity.program_id == program_id, Identity.profile_id == profile.id).with_for_update().execution_options(populate_existing=True)))
    if (row.version if row else 0) != expected_version:
        raise s1.ClassroomDenied('The school name changed. Reopen the roster before correcting it.')
    if row and row.school_name == name:
        return {'identity_version': row.version, 'changed': False}
    now = s2._now()
    if row is None:
        row = Identity(program_id=program_id, profile_id=profile.id, school_name=name, version=1, updated_at=now)
        session.add(row); session.flush()
    else:
        row.school_name, row.version, row.updated_at = name, row.version + 1, now
    session.add(Audit(identity_id=row.id, program_id=program_id, profile_id=profile.id,
        class_id=class_id, actor_verifier_id=actor_id, version=row.version,
        action='created' if row.version == 1 else 'corrected', occurred_at=now))
    session.flush()
    return {'identity_version': row.version, 'changed': True}
