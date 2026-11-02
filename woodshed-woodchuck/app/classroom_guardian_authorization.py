"""Class-specific parent choices, grounded in existing activated KWS/account evidence.

Adult verification, legal-guardian attestation, account consent, participation,
student reporting choice and guardian reporting consent are distinct predicates.
No school authority, KWS result, parent identity, membership or chart share is issued.
"""
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import hmac
import json
import os

from sqlalchemy import select

from . import classroom as s1, classroom_s2 as s2, classroom_reporting as boundary
from . import child_authorization as account_consent, parent_access
from .age_models import AccountPrivacy
from .age_privacy import utc
from .child_models import ConsentEvidence, ParentAccess, PendingConsent
from .classroom_guardian_models import (
    ClassroomGuardianAuthorization as Authorization, ClassroomGuardianRecipient as Recipient,
    ClassroomGuardianAudit as Audit,
)
from .classroom_models import ClassroomEntitlement
from .kws_models import KWSVerification
from .session_config import session_secret

UNAVAILABLE = 'Classroom parent authorization unavailable.'
ATTESTATION_VERSION = 'classroom-guardian-attestation-v1'
ATTESTATION = "I am this child's parent or legal guardian and am authorized to decide these permissions."
PARTICIPATION_VERSION = 'classroom-guardian-participation-v1'
REPORTING_VERSION = 'classroom-guardian-reporting-v1'
PARTICIPATION_NOTICE = (
    'I authorize my child to participate in this Class in the named Music Program. '
    'Its assigned directors and authorized Program administrators may use the existing private '
    'school name, WC#, instrument and membership records for this Class. '
    'This choice does not enroll my child, authorize comprehensive reporting, share individual '
    'P-Charts, create a Connected Student relationship or make private information public. '
    'I can withdraw this permission. Membership records and separately authorized private practice remain.'
)
REPORTING_NOTICE = (
    'I authorize only the listed current Band Director(s) for this exact Class and Music Program '
    "to receive the 41 approved individual reporting fields listed below, from this authorization's "
    'effective period forward. Earlier private history is excluded. '
    'My child must also explicitly choose Classroom reporting. Participation does not require reporting. '
    'Individual P-Charts retain separate permissions. Comprehensive notes, exports, cohort reporting '
    'and the other 26 excluded fields are not authorized. I can withdraw reporting independently. '
    'A changed Class, membership period, entitlement, account permission or recipient set requires a fresh choice.'
)
_SEAL = object()


def enabled():
    # Participation and its withdrawal must not depend on reporting being on.
    return (s2.enabled() and os.getenv('CLASSROOM_GUARDIAN_AUTHORIZATION_ENABLED') == '1')


def _operation(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        if not enabled():
            raise s1.ClassroomDenied(UNAVAILABLE)
        return function(session, *args, **kwargs)
    return s1._mutation(checked)


def _notice(purpose):
    if purpose == 'participation':
        version, text, contract = PARTICIPATION_VERSION, PARTICIPATION_NOTICE, None
    elif purpose == 'reporting':
        from .classroom_comprehensive_reporting import CONTRACT_VERSION, FIELD_LABELS
        version, text, contract = REPORTING_VERSION, REPORTING_NOTICE, CONTRACT_VERSION
        # Bind the exact fixed fields as well as the prose. No input-menu expansion.
        text += '\n' + '\n'.join(label for _, label in FIELD_LABELS)
    else:
        raise s1.ClassroomDenied(UNAVAILABLE)
    digest = hashlib.sha256((ATTESTATION_VERSION + '\n' + ATTESTATION + '\n' + version + '\n' + text).encode()).hexdigest()
    return version, text, digest, contract


def required(session, profile_id):
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    return bool(rule and (rule.age_band == 'under13' or rule.consent_id))


def _qualified_account(session, profile_id, now, *, lock=True):
    """Read original server-accepted activation evidence, never email/role assertions."""
    from . import kws_verification as kws, kws_client
    if lock:
        profile = s2._eligible_profile(session, profile_id)
    else:
        from .age_privacy import eligible
        from .models import WoodchuckProfile
        profile = session.get(WoodchuckProfile, profile_id, populate_existing=True)
        if profile is None or not eligible(session, profile_id):
            raise s1.ClassroomDenied(UNAVAILABLE)
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    if rule is None or rule.age_band not in {'under13', '13to17', 'adult'} or not rule.consent_id:
        raise s1.ClassroomDenied(UNAVAILABLE)
    evidence = session.get(ConsentEvidence, rule.consent_id, populate_existing=True)
    verifications = session.scalars(select(KWSVerification).where(
        KWSVerification.activated_consent_id == rule.consent_id)
        .execution_options(populate_existing=True).limit(2)).all()
    if evidence is None or len(verifications) != 1:
        raise s1.ClassroomDenied(UNAVAILABLE)
    verification = verifications[0]
    pending = session.get(PendingConsent, verification.pending_id, populate_existing=True)
    try:
        cfg = kws_client.Config.load(verification.environment)
        version, _, digest = account_consent.notice_policy(verification.environment)
        valid = (
            verification.environment == kws_client.runtime_environment()
            and verification.org_id == cfg.org_id and verification.product_id == cfg.product_id
            and verification.state == 'activated' and verification.account_allowed is True
            and verification.transaction_hash is not None and len(verification.transaction_hash) == 64
            and verification.completed_at is not None and pending is not None
            and pending.profile_id in (None, profile_id)
            and pending.notice_version == evidence.notice_version == version
            and verification.notice_sha256 == evidence.notice_sha256 == digest
            and evidence.activation_hash is not None and evidence.activation_hash == pending.activation_hash
            and evidence.parent_email is not None
            and hmac.compare_digest(evidence.parent_email.casefold().encode(), pending.parent_email.casefold().encode())
            and utc(verification.guardian_attested_at) <= utc(verification.notice_accepted_at)
            <= utc(verification.completed_at) <= now
            and utc(evidence.approved_at) == utc(verification.notice_accepted_at)
            and utc(evidence.confirmed_at) == utc(verification.completed_at)
            and hmac.compare_digest(verification.binding_sha256, kws.binding(pending, verification))
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid or evidence.profile_id != profile_id or evidence.withdrawn_at is not None:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return profile, rule, evidence, verification


@dataclass(frozen=True, repr=False)
class ParentAuthentication:
    session: object = field(repr=False)
    transaction: object = field(repr=False)
    consent_id: int = field(repr=False)
    profile_id: int = field(repr=False)
    credential_hash: str = field(repr=False)
    seal: object = field(repr=False)


def parent_from_request(session, request):
    if not enabled():
        raise s1.ClassroomDenied(UNAVAILABLE)
    try:
        evidence, _, profile = parent_access.authenticated(session, request)
    except ValueError as error:
        raise s1.ClassroomDenied(UNAVAILABLE) from error
    access = session.get(ParentAccess, evidence.id, populate_existing=True)
    return ParentAuthentication(session, session.get_transaction(), evidence.id, profile.id,
                                access.session_hash, _SEAL)


def _parent(session, parent):
    if (type(parent) is not ParentAuthentication or parent.seal is not _SEAL
            or parent.session is not session or parent.transaction is not session.get_transaction()
            or parent.transaction is None or not parent.transaction.is_active):
        raise s1.ClassroomDenied(UNAVAILABLE)
    _, _, evidence, _ = _qualified_account(session, parent.profile_id, boundary._now())
    access = session.get(ParentAccess, evidence.id, populate_existing=True)
    if (evidence.id != parent.consent_id or access is None or access.session_hash is None
            or access.session_expires_at is None or utc(access.session_expires_at) <= account_consent.clock()
            or not hmac.compare_digest(access.session_hash, parent.credential_hash)):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return evidence


def token_binding(parent):
    # Never send the native stored parent credential hash to the browser.
    return hmac.new(session_secret().encode(), ('classroom-parent-form:' + parent.credential_hash).encode(),
                    hashlib.sha256).hexdigest()


def _context(session, program_id, class_id, profile_id):
    boundary._exact_ids(program_id, class_id, profile_id)
    s1._lock_program(session, program_id)
    state = s2._active(session, program_id, class_id)
    _, rule, evidence, verification = _qualified_account(session, profile_id, boundary._now())
    anchor = s2._anchor(session, class_id, profile_id)
    if anchor is None or s2._held(session, anchor.id):
        raise s1.ClassroomDenied(UNAVAILABLE)
    member = next((row for row in s2._validated_periods(session, anchor.id)
        if row.state == 'active' and row.ended_at is None and utc(row.starts_at) <= boundary._now()), None)
    now = boundary._now()  # after Program, consent and profile lock waits
    entitlement = s2.entitlement_decision(session, program_id, at=now)
    activation = boundary._activation(session, state)
    if member is None or not entitlement.allowed or activation > now or utc(rule.declared_at) > now:
        raise s1.ClassroomDenied(UNAVAILABLE)
    package = session.get(ClassroomEntitlement, entitlement.entitlement_id, populate_existing=True)
    return (anchor, member, package, rule, activation, now), evidence, verification


def _latest(session, program_id, class_id, profile_id, purpose):
    return session.scalar(select(Authorization).where(
        Authorization.program_id == program_id, Authorization.class_id == class_id,
        Authorization.profile_id == profile_id, Authorization.purpose == purpose)
        .order_by(Authorization.id.desc()).limit(1).execution_options(populate_existing=True))


def _revision(row):
    return (row.id, utc(row.withdrawn_at).isoformat() if row.withdrawn_at else None) if row else (None, None)


def _base_revision(session, context):
    from .classroom_progress_authorization import _source_revision_base
    return _source_revision_base(session, context)


def _current(session, row, context, evidence, verification, purpose, participation=None):
    from .classroom_progress_authorization import _teachers
    anchor, member, package, rule, activation, now = context
    version, _, digest, contract = _notice(purpose)
    if not (row and row.withdrawn_at is None and utc(row.accepted_at) <= now < utc(row.expires_at)
            and row.program_id == package.program_id and row.class_id == anchor.class_id
            and row.profile_id == anchor.profile_id == evidence.profile_id
            and row.consent_id == evidence.id and row.verification_id == verification.id
            and row.membership_period_id == member.id and row.entitlement_id == package.id
            and row.purpose == purpose and row.field_contract == contract
            and row.notice_version == version and row.notice_digest == digest
            and row.attestation_version == ATTESTATION_VERSION
            and utc(row.guardian_attested_at) == utc(row.notice_accepted_at) == utc(row.accepted_at)
            and row.source_revision == _base_revision(session, context)):
        return False
    if session.scalar(select(Audit.id).where(Audit.authorization_id == row.id,
            Audit.program_id == row.program_id, Audit.class_id == row.class_id,
            Audit.profile_id == row.profile_id, Audit.actor_consent_id == evidence.id,
            Audit.action == 'accepted', Audit.occurred_at == row.accepted_at)) is None:
        return False
    if purpose == 'reporting':
        recipients = set(session.scalars(select(Recipient.assignment_id).where(
            Recipient.authorization_id == row.id, Recipient.program_id == row.program_id,
            Recipient.class_id == row.class_id, Recipient.profile_id == row.profile_id)))
        teachers = _teachers(session, row.program_id, row.class_id, now)
        return bool(participation and row.participation_id == participation.id and teachers
                    and recipients == {teacher.id for teacher in teachers})
    return row.participation_id is None


def _grants(session, context, evidence, verification, *, reporting):
    anchor, _, package, _, _, _ = context
    participation = _latest(session, package.program_id, anchor.class_id, anchor.profile_id, 'participation')
    if not _current(session, participation, context, evidence, verification, 'participation'):
        raise s1.ClassroomDenied(UNAVAILABLE)
    if not reporting:
        return (participation,)
    report = _latest(session, package.program_id, anchor.class_id, anchor.profile_id, 'reporting')
    if not _current(session, report, context, evidence, verification, 'reporting', participation):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return participation, report


def reporting_evidence(session, program_id, class_id, profile_id):
    if not enabled():
        raise s1.ClassroomDenied(UNAVAILABLE)
    context, evidence, verification = _context(session, program_id, class_id, profile_id)
    if context[3].age_band == 'under13':
        raise s1.ClassroomDenied(UNAVAILABLE)
    return _grants(session, context, evidence, verification, reporting=True)


def participation_allowed(session, program_id, class_id, profile_id):
    if not enabled() or not required(session, profile_id):
        return False
    try:
        context, evidence, verification = _context(session, program_id, class_id, profile_id)
        _grants(session, context, evidence, verification, reporting=False)
        return True
    except s1.ClassroomDenied:
        return False


def participation_source_allowed(session, context):
    """Read-only source projection, not an authority token or new lock order.

    Capability callers already validate the exact active source. Game/account
    mutations retain their native consent/profile fences and recheck this source.
    In particular this projection must not acquire Program locks under a profile.
    """
    if not enabled():
        return False
    try:
        anchor, _, _, _, _, now = context
        _, _, evidence, verification = _qualified_account(session, anchor.profile_id, now, lock=False)
        _grants(session, context, evidence, verification, reporting=False)
        return True
    except s1.ClassroomDenied:
        return False


def _audit(session, row, consent_id, action, now):
    session.add(Audit(authorization_id=row.id, program_id=row.program_id, class_id=row.class_id,
        profile_id=row.profile_id, actor_consent_id=consent_id, action=action, occurred_at=now))
    session.flush()


def _close(session, row, consent_id, now, reason):
    row.withdrawn_at = max(utc(row.accepted_at), now)
    row.withdrawal_reason = reason
    _audit(session, row, consent_id, reason, row.withdrawn_at)


@_operation
def choice(session, *, parent, program_id, class_id, purpose):
    _notice(purpose)
    boundary._exact_ids(program_id, class_id)
    s1._lock_program(session, program_id)
    evidence = _parent(session, parent)
    row = _latest(session, program_id, class_id, parent.profile_id, purpose)
    under13_reporting = purpose == 'reporting' and session.get(AccountPrivacy, parent.profile_id).age_band == 'under13'
    result = {'eligible': False, 'authorized': False, 'saved': bool(row and row.withdrawn_at is None),
              'under13_reporting': under13_reporting,
              'revision': _revision(row), 'assignment_ids': (), 'source_revision': None, 'participation_id': None}
    if under13_reporting:
        return result
    try:
        context, evidence, verification = _context(session, program_id, class_id, parent.profile_id)
        participation = _grants(session, context, evidence, verification, reporting=False)[0] if purpose == 'reporting' else None
    except s1.ClassroomDenied:
        return result
    from .classroom_progress_authorization import _teachers
    teachers = _teachers(session, program_id, class_id, context[-1])
    result.update(eligible=bool(teachers) if purpose == 'reporting' else True,
        authorized=_current(session, row, context, evidence, verification, purpose, participation),
        assignment_ids=tuple(t.id for t in teachers), source_revision=_base_revision(session, context),
        participation_id=participation.id if participation else None)
    return result


@_operation
def accept(session, *, parent, program_id, class_id, purpose, notice_version, notice_digest,
           guardian_attested, opted_in, previous, source_revision, assignment_ids, participation_id):
    version, _, digest, contract = _notice(purpose)
    if (guardian_attested is not True or opted_in is not True or notice_version != version
            or notice_digest != digest or type(assignment_ids) is not tuple
            or any(type(i) is not int for i in assignment_ids)):
        raise s1.ClassroomDenied(UNAVAILABLE)
    boundary._exact_ids(program_id, class_id)
    s1._lock_program(session, program_id)
    evidence = _parent(session, parent)
    context, evidence, verification = _context(session, program_id, class_id, parent.profile_id)
    if purpose == 'reporting' and context[3].age_band == 'under13':
        raise s1.ClassroomDenied(UNAVAILABLE)
    from .classroom_progress_authorization import _teachers
    teachers = _teachers(session, program_id, class_id, context[-1])
    part = _grants(session, context, evidence, verification, reporting=False)[0] if purpose == 'reporting' else None
    if (assignment_ids != tuple(t.id for t in teachers) or purpose == 'reporting' and not teachers
            or source_revision != _base_revision(session, context)
            or participation_id != (part.id if part else None)):
        raise s1.ClassroomDenied(UNAVAILABLE)
    old = _latest(session, program_id, class_id, parent.profile_id, purpose)
    if previous != _revision(old):
        raise s1.ClassroomDenied(UNAVAILABLE)
    if _current(session, old, context, evidence, verification, purpose, part):
        return old
    now = context[-1]
    if old and utc(old.withdrawn_at or old.accepted_at) > now:
        raise s1.ClassroomDenied(UNAVAILABLE)
    if old and old.withdrawn_at is None:
        _close(session, old, evidence.id, now, 'replaced')
    row = Authorization(program_id=program_id, class_id=class_id, profile_id=parent.profile_id,
        consent_id=evidence.id, verification_id=verification.id, membership_period_id=context[1].id,
        entitlement_id=context[2].id, purpose=purpose, participation_id=part.id if part else None,
        field_contract=contract, notice_version=version, notice_digest=digest,
        attestation_version=ATTESTATION_VERSION, source_revision=source_revision,
        guardian_attested_at=now, notice_accepted_at=now, accepted_at=now,
        expires_at=utc(context[2].ends_at))
    session.add(row); session.flush()
    if purpose == 'reporting':
        session.add_all(Recipient(authorization_id=row.id, assignment_id=t.id,
            program_id=program_id, class_id=class_id, profile_id=parent.profile_id) for t in teachers)
    _audit(session, row, evidence.id, 'accepted', now)
    _parent(session, parent)
    return row


@_operation
def withdraw(session, *, parent, program_id, class_id, purpose):
    _notice(purpose)
    boundary._exact_ids(program_id, class_id)
    s1._lock_program(session, program_id)
    evidence = _parent(session, parent)
    row = _latest(session, program_id, class_id, parent.profile_id, purpose)
    if row and row.withdrawn_at is None:
        _close(session, row, evidence.id, boundary._now(), 'withdrawn')
    return row


def authorize_school(*args, **kwargs):
    """An organizational agreement cannot substitute for child/guardian consent."""
    raise s1.ClassroomDenied('A school agreement does not replace individual guardian authorization.')
