"""Applicable parent permission for one explicitly selected individual P-Chart.

The proposed Class surface reuses only an exact, native, parent-authorized named
director's existing chart-view permission. It creates no permission or connection.
This new use remains closed until its legal scope is reviewed; ordinary eligible
13+ sharing keeps its accepted boundary. School approval is never consulted.
"""
import os

from sqlalchemy import select

from . import classroom as s1, classroom_guardian_authorization as guardian
from . import classroom_reporting as reporting, child_authorization as consent
from .child_models import DirectorPermission
from .classroom_models import ClassroomTeachingAssignment
from .models import StudentVerifierConnection, TrustedVerifier

POLICY_VERSION = 'classroom-named-guardian-pchart-draft-v1'
LEGALLY_APPROVED_POLICIES = frozenset()


def account_rule(session, profile_id, *, lock=False):
    if not guardian.required(session, profile_id):
        return reporting._account_rule(session, profile_id)
    if (os.getenv('CLASSROOM_GUARDIAN_PCHART_SHARING_ENABLED') != '1'
            or POLICY_VERSION not in LEGALLY_APPROVED_POLICIES or not guardian.enabled()):
        raise s1.ClassroomDenied('Classroom sharing unavailable.')
    return guardian._qualified_account(session, profile_id, reporting._now(), lock=lock)[1]


def permission(session, profile_id, assignment_id, *, lock=False):
    rule = account_rule(session, profile_id, lock=lock)
    if not guardian.required(session, profile_id):
        return None
    assignment = session.get(ClassroomTeachingAssignment, assignment_id, populate_existing=True)
    if assignment is None:
        raise s1.ClassroomDenied('Classroom sharing unavailable.')
    # Fresh objects after the consent/profile fence. Do not add permission-row
    # locks beneath account locks: native emailed-link consumption has its own
    # established order. Parent revocation uses the same consent fence as this
    # reader; the ordinary source projection remains read only.
    rows = session.execute(select(DirectorPermission, StudentVerifierConnection, TrustedVerifier)
        .join(StudentVerifierConnection, StudentVerifierConnection.id == DirectorPermission.connection_id)
        .join(TrustedVerifier, TrustedVerifier.id == StudentVerifierConnection.verifier_id)
        .where(DirectorPermission.profile_id == profile_id, DirectorPermission.consent_id == rule.consent_id,
            DirectorPermission.revoked_at.is_(None), StudentVerifierConnection.profile_id == profile_id,
            StudentVerifierConnection.verifier_id == assignment.verifier_id,
            StudentVerifierConnection.status == 'accepted', StudentVerifierConnection.role == 'band_director')
        .execution_options(populate_existing=True)).all()
    valid = [permission for permission, _, verifier in rows
        if permission.email.casefold() == verifier.email.casefold()
        and consent.permission_valid(session, permission, require_connection=True)]
    if len(valid) != 1:
        raise s1.ClassroomDenied('Classroom sharing unavailable.')
    return valid[0]
