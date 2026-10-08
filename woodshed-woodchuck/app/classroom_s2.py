"""Program Classroom entry and membership only; no consumer or reporting grants.

All writers use S1's savepoint and Program-first lock. Callers own the outer
transaction. PostgreSQL READ COMMITTED is the concurrent production contract.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
import base64
import hashlib
import hmac
import json
import os
import re
import secrets

from sqlalchemy import func, select

from . import classroom as s1
from .age_privacy import eligible, utc
from .classroom_models import (
    ClassroomClass, ClassroomClassState, ClassroomEntitlement, ClassroomEntryCode,
    ClassroomMembershipHold, ClassroomMembershipPeriod, ClassroomS2AuditEvent,
    ClassroomStudentMembership, ClassroomTeachingAssignment,
)
from .models import WoodchuckProfile
from .session_config import session_secret

ClassroomDenied = s1.ClassroomDenied
TRIAL_LENGTH = timedelta(days=14)
INTENT_LENGTH = timedelta(minutes=30)
_SEAL = object()
INVALID_CODE = "Class entry is unavailable. Use the current Music Program and Class code."


def _now():
    return datetime.now(timezone.utc)


def enabled():
    return s1.enabled() and os.getenv("CLASSROOM_S2_ENABLED") == "1"


def _require_enabled():
    if not enabled():
        raise ClassroomDenied("Classroom S2 is disabled.")


def _mutation(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        _require_enabled()
        return function(session, *args, **kwargs)
    return s1._mutation(checked)


def default_allowances():
    """Internal working configuration, deliberately not a pricing catalog."""
    try:
        classes = int(os.getenv("CLASSROOM_ACTIVE_CLASS_LIMIT", "10"))
        teachers = int(os.getenv("CLASSROOM_TEACHING_DIRECTOR_LIMIT", "2"))
    except ValueError as error:
        raise ClassroomDenied("Invalid Classroom allowance configuration.") from error
    _limits(classes, teachers)
    return classes, teachers


def _limits(classes, teachers):
    if (type(classes) is not int or type(teachers) is not int
            or not 0 <= classes <= 10000 or not 0 <= teachers <= 10000):
        raise ClassroomDenied("Classroom allowances must be bounded nonnegative integers.")


@dataclass(frozen=True)
class EntitlementDecision:
    allowed: bool
    reason: str
    source: str | None = None
    entitlement_id: int | None = None
    class_limit: int = 0
    teacher_limit: int = 0


def usage(session, program_id):
    classes = session.scalar(select(func.count()).select_from(ClassroomClassState).where(
        ClassroomClassState.program_id == program_id, ClassroomClassState.state == "active"))
    return classes, len(_teaching_adults(session, program_id))


def _teaching_adults(session, program_id, *, activating_class=None):
    active_classes = select(ClassroomClassState.class_id).where(
        ClassroomClassState.program_id == program_id, ClassroomClassState.state == "active")
    scope = ClassroomTeachingAssignment.class_id.in_(active_classes)
    if activating_class is not None:
        scope = scope | (ClassroomTeachingAssignment.class_id == activating_class)
    return set(session.scalars(select(ClassroomTeachingAssignment.verifier_id).where(
        ClassroomTeachingAssignment.program_id == program_id, scope,
        ClassroomTeachingAssignment.starts_at <= _now(), ClassroomTeachingAssignment.ended_at.is_(None))))


def entitlement_decision(session, program_id, *, at=None):
    if not enabled():
        return EntitlementDecision(False, "disabled")
    now = at or _now()
    rows = list(session.scalars(select(ClassroomEntitlement).where(
        ClassroomEntitlement.program_id == program_id).order_by(ClassroomEntitlement.id.desc())
        .execution_options(populate_existing=True)))
    valid = [r for r in rows if r.status == "active" and utc(r.starts_at) <= now < utc(r.ends_at)]
    # An approved package explicitly takes precedence over the ordinary trial.
    row = next((r for r in valid if r.source == "institutional"), valid[0] if valid else None)
    if row is None:
        reason = "expired" if any(utc(r.ends_at) <= now for r in rows) else "inactive" if rows else "missing"
        return EntitlementDecision(False, reason)
    classes, teachers = usage(session, program_id)
    over = classes > row.class_limit or teachers > row.teacher_limit
    return EntitlementDecision(not over, "over_limit" if over else "active", row.source,
                               row.id, row.class_limit, row.teacher_limit)


def _paid(session, program_id):
    decision = entitlement_decision(session, program_id)
    if not decision.allowed:
        raise ClassroomDenied("Program Classroom access unavailable: " + decision.reason)
    return decision


def _audit(session, program_id, action, *, actor=None, student=None, admin=None, **refs):
    session.add(ClassroomS2AuditEvent(program_id=program_id, action=action,
        actor_verifier_id=actor, actor_profile_id=student, actor_admin=admin, occurred_at=_now(), **refs))


@_mutation
def start_trial(session, *, actor, program_id):
    program = s1._lock_program(session, program_id)
    actor_id = s1._actor(session, actor)
    if actor_id != program.owner_verifier_id and "head_director" not in s1._roles(session, program_id, actor_id):
        raise ClassroomDenied("Current Owner or Head Director authority is required.")
    if session.scalar(select(ClassroomEntitlement.id).where(
            ClassroomEntitlement.program_id == program_id, ClassroomEntitlement.source == "trial")):
        raise ClassroomDenied("This Program has already used its ordinary trial.")
    classes, teachers = default_allowances()
    now = _now()
    row = ClassroomEntitlement(program_id=program_id, source="trial", status="active",
        starts_at=now, ends_at=now + TRIAL_LENGTH, class_limit=classes, teacher_limit=teachers,
        approved_by_verifier_id=actor_id, provenance="owner_head_director_request", created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    _audit(session, program_id, "trial_started", actor=actor_id, entitlement_id=row.id)
    return row


@dataclass(frozen=True, repr=False)
class SiteAdminAuthentication:
    request: object = field(repr=False)
    fingerprint: str = field(repr=False)
    seal: object = field(repr=False)


def site_admin_authentication(request):
    from .site_admin import require_site_admin
    require_site_admin(request)
    return SiteAdminAuthentication(request, request.session["site_admin_fingerprint"], _SEAL)


def _admin(admin):
    from .site_admin import require_site_admin
    if type(admin) is not SiteAdminAuthentication or admin.seal is not _SEAL:
        raise ClassroomDenied("Site administrator authentication is required.")
    require_site_admin(admin.request)
    if admin.request.session.get("site_admin_fingerprint") != admin.fingerprint:
        raise ClassroomDenied("Site administrator authentication changed.")
    return admin.fingerprint


@_mutation
def configure_institutional(session, *, admin, program_id, starts_at, ends_at,
                            class_limit, teacher_limit, provenance, status="active"):
    s1._lock_program(session, program_id)
    approver = _admin(admin)
    _limits(class_limit, teacher_limit)
    if (not isinstance(starts_at, datetime) or not isinstance(ends_at, datetime)
            or starts_at.tzinfo is None or ends_at.tzinfo is None or ends_at <= starts_at
            or status not in {"active", "ended"}
            or not isinstance(provenance, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", provenance)):
        raise ClassroomDenied("Supply a finite entitlement period and an internal approval reference.")
    # SQLite drops timezone offsets on persistence; store the approved instants
    # in UTC so a fresh Session makes the same decision on either backend.
    starts_at, ends_at = utc(starts_at), utc(ends_at)
    now = _now()
    previous = list(session.scalars(select(ClassroomEntitlement).where(
        ClassroomEntitlement.program_id == program_id, ClassroomEntitlement.source == "institutional",
        ClassroomEntitlement.status == "active")))
    for row in previous:
        row.status, row.ended_at, row.updated_at = "ended", now, now
        _audit(session, program_id, "institutional_ended", admin=approver, entitlement_id=row.id,
               reason="replaced" if status == "active" else "admin_ended")
    if status == "ended":
        if not previous:
            raise ClassroomDenied("No active institutional arrangement exists.")
        return previous[-1]
    row = ClassroomEntitlement(program_id=program_id, source="institutional", status="active",
        starts_at=starts_at, ends_at=ends_at, class_limit=class_limit, teacher_limit=teacher_limit,
        approved_by_admin=approver, provenance=provenance, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    _audit(session, program_id, "institutional_changed" if previous else "institutional_created",
           admin=approver, entitlement_id=row.id)
    return row


def _class_state(session, program_id, class_id):
    s1._class(session, program_id, class_id)
    return s1._fresh(session, ClassroomClassState, ClassroomClassState.class_id == class_id,
                     ClassroomClassState.program_id == program_id)


def _active(session, program_id, class_id, *, enrollment=False):
    state = _class_state(session, program_id, class_id)
    if state is None or state.state != "active" or enrollment and not state.enrollment_open:
        raise ClassroomDenied(INVALID_CODE)
    return state


@_mutation
def create_class(session, *, actor, program_id, display_name):
    program = s1._lock_program(session, program_id)
    actor_id = s1._authority(session, program, actor, admin=True)
    decision = _paid(session, program_id)
    if usage(session, program_id)[0] >= decision.class_limit:
        raise ClassroomDenied("Active Class allowance reached.")
    row = ClassroomClass(program_id=program_id, display_name=s1._name(display_name, 150))
    session.add(row)
    session.flush()
    now = _now()
    session.add(ClassroomClassState(class_id=row.id, program_id=program_id, state="active",
        enrollment_open=True, changed_by_verifier_id=actor_id, created_at=now, updated_at=now))
    _audit(session, program_id, "class_activated", actor=actor_id, class_id=row.id,
           entitlement_id=decision.entitlement_id)
    return row


def _revoke_code(session, program_id, class_id, actor_id):
    code = session.scalar(select(ClassroomEntryCode).where(
        ClassroomEntryCode.class_id == class_id, ClassroomEntryCode.is_current.is_(True)))
    if code:
        code.is_current, code.revoked_at, code.revoked_by_verifier_id = False, _now(), actor_id
        _audit(session, program_id, "code_revoked", actor=actor_id, class_id=class_id, code_id=code.id)
        session.flush()


@_mutation
def set_class_state(session, *, actor, program_id, class_id, state):
    if state not in {"active", "archived"}:
        raise ClassroomDenied("Choose active or archived.")
    program = s1._lock_program(session, program_id)
    actor_id = s1._authority(session, program, actor, admin=True)
    row = _class_state(session, program_id, class_id)
    if row is not None and row.state == state:
        return row
    now = _now()
    if state == "active":
        decision = _paid(session, program_id)
        if usage(session, program_id)[0] >= decision.class_limit:
            raise ClassroomDenied("Active Class allowance reached.")
        if len(_teaching_adults(session, program_id, activating_class=class_id)) > decision.teacher_limit:
            raise ClassroomDenied("Teaching director allowance reached.")
    previous = row is not None
    if row is None:
        row = ClassroomClassState(class_id=class_id, program_id=program_id, created_at=now)
        session.add(row)
    row.state, row.enrollment_open = state, False
    row.updated_at, row.changed_by_verifier_id = now, actor_id
    # Reactivation starts closed and without a code. A separate deliberate code
    # issuance is fresh authority; neither old code nor held membership revives.
    _revoke_code(session, program_id, class_id, actor_id)
    action = "class_archived" if state == "archived" else "class_reactivated" if previous else "class_activated"
    _audit(session, program_id, action, actor=actor_id, class_id=class_id,
           entitlement_id=_paid(session, program_id).entitlement_id if state == "active" else None)
    return row


@_mutation
def set_enrollment(session, *, actor, program_id, class_id, open):
    if type(open) is not bool:
        raise ClassroomDenied("Choose an explicit enrollment state.")
    program = s1._lock_program(session, program_id)
    actor_id = s1._authority(session, program, actor, admin=True)
    row = _active(session, program_id, class_id)
    if open:
        _paid(session, program_id)
    if row.enrollment_open != open:
        row.enrollment_open, row.updated_at, row.changed_by_verifier_id = open, _now(), actor_id
        _audit(session, program_id, "enrollment_opened" if open else "enrollment_closed",
               actor=actor_id, class_id=class_id,
               entitlement_id=_paid(session, program_id).entitlement_id if open else None)
    return row


def normalize_code(value):
    # Only U+0020 may surround a code. Controls (including tabs/newlines) and all
    # non-ASCII are rejected BEFORE case conversion. No separators are accepted.
    if (not isinstance(value, str) or len(value) > 64 or not value.isascii()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ClassroomDenied(INVALID_CODE)
    normalized = value.strip(" ").upper()
    if not re.fullmatch(r"[A-Z0-9]{6,24}", normalized):
        raise ClassroomDenied(INVALID_CODE)
    return normalized


def _key(purpose):
    return hmac.new(session_secret().encode(), purpose.encode(), hashlib.sha256).digest()


def _digest(program_id, code):
    return hmac.new(_key("classroom-entry-code:v1"),
                    f"{program_id}:{normalize_code(code)}".encode("ascii"), hashlib.sha256).hexdigest()


def _generate_code():
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))


@_mutation
def issue_code(session, *, actor, program_id, class_id, preferred=None):
    program = s1._lock_program(session, program_id)
    actor_id = s1._actor(session, actor)
    if (actor_id != program.owner_verifier_id
            and not {"admin", "code_manager"}.intersection(s1._roles(session, program_id, actor_id))):
        raise ClassroomDenied("Class code management authority is required.")
    _paid(session, program_id)
    _active(session, program_id, class_id)
    for _ in range(8):
        visible = normalize_code(preferred if preferred is not None else _generate_code())
        digest = _digest(program_id, visible)
        collision = session.scalar(select(ClassroomEntryCode.id).where(
            ClassroomEntryCode.program_id == program_id, ClassroomEntryCode.digest == digest,
            ClassroomEntryCode.is_current.is_(True)))
        if collision is None:
            break
        if preferred is not None:
            raise ClassroomDenied(INVALID_CODE)
    else:
        raise ClassroomDenied(INVALID_CODE)
    generation = (session.scalar(select(func.max(ClassroomEntryCode.generation)).where(
        ClassroomEntryCode.class_id == class_id)) or 0) + 1
    _revoke_code(session, program_id, class_id, actor_id)
    row = ClassroomEntryCode(program_id=program_id, class_id=class_id, generation=generation,
        digest=digest, is_current=True, issued_by_verifier_id=actor_id, issued_at=_now())
    session.add(row)
    session.flush()
    _audit(session, program_id, "code_rotated" if generation > 1 else "code_issued",
           actor=actor_id, class_id=class_id, code_id=row.id,
           entitlement_id=_paid(session, program_id).entitlement_id)
    return row, visible


@dataclass(frozen=True, repr=False)
class StudentAuthentication:
    session: object = field(repr=False)
    transaction: object = field(repr=False)
    profile_id: int = field(repr=False)
    credential_hash: str = field(repr=False)
    session_version: int = field(repr=False)
    seal: object = field(repr=False)


def _student_proof(session, profile):
    return StudentAuthentication(session, session.get_transaction(), profile.id,
                                 profile.pin_hash, profile.session_version, _SEAL)


def authenticate_student(session, *, woodchuck_id, pin):
    from .accounts import authenticate_woodchuck
    session.expire_all()
    profile = authenticate_woodchuck(session, woodchuck_id=woodchuck_id, pin=pin)
    if profile is None:
        raise ClassroomDenied("Student sign-in is required.")
    return _student_proof(session, profile)


def student_from_request(session, request):
    """Same existing student session; delayed eligibility is solely for entry intent.

    This does not authenticate a Guest or create an account. Final join separately
    locks/rechecks current account authorization. No account/KWS payload changes.
    """
    from .account_routes import SESSION_PROFILE_ID, SESSION_PROFILE_VERSION
    pid = request.session.get(SESSION_PROFILE_ID)
    version = request.session.get(SESSION_PROFILE_VERSION, 0)
    profile = session.get(WoodchuckProfile, pid, populate_existing=True) if type(pid) is int else None
    if (profile is None or profile.status != "active" or type(version) is not int
            or version != profile.session_version
            or request.headers.get("x-woodshed-account", profile.woodchuck_id) != profile.woodchuck_id):
        raise ClassroomDenied("Student sign-in is required.")
    return _student_proof(session, profile)


def _student(session, student, *, require_eligibility=True):
    if (type(student) is not StudentAuthentication or student.seal is not _SEAL
            or student.session is not session or student.transaction is not session.get_transaction()
            or student.transaction is None or not student.transaction.is_active):
        raise ClassroomDenied("Current student authentication is required.")
    profile = _eligible_profile(session, student.profile_id, require_eligibility=require_eligibility)
    if profile.pin_hash != student.credential_hash or profile.session_version != student.session_version:
        raise ClassroomDenied("Student sign-in changed. Sign in again.")
    return profile


def _eligible_profile(session, profile_id, *, require_eligibility=True):
    from .age_models import AccountPrivacy
    from .child_models import ConsentEvidence
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    consent_id = rule.consent_id if rule else None
    # Existing withdrawal locks consent then profile; follow that order, never
    # acquire PTA/season locks here (S2 has no Team mutation).
    if consent_id:
        s1._fresh(session, ConsentEvidence, ConsentEvidence.id == consent_id)
    profile = s1._fresh(session, WoodchuckProfile, WoodchuckProfile.id == profile_id)
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    if (profile is None or profile.status != "active"
            or (rule.consent_id if rule else None) != consent_id
            or require_eligibility and not eligible(session, profile_id)):
        raise ClassroomDenied("Current account and parent authorization are required.")
    return profile


def _entry(session, program_id, *, code=None, intent=None, student=None):
    # Public entry authority must not disclose Program existence or entitlement.
    # Account eligibility and membership holds are checked separately after this.
    try:
        s1._lock_program(session, program_id)
        return _entry_authority(session, program_id, code=code, intent=intent, student=student)
    except ClassroomDenied as error:
        raise ClassroomDenied(INVALID_CODE) from error


def _entry_authority(session, program_id, *, code=None, intent=None, student=None):
    _paid(session, program_id)
    if (code is None) == (intent is None):
        raise ClassroomDenied(INVALID_CODE)
    if intent is not None:
        if type(student) is not StudentAuthentication or student.seal is not _SEAL:
            raise ClassroomDenied(INVALID_CODE)
        payload = _parse_intent(intent)
        if payload["program"] != program_id or payload["student"] != student.profile_id:
            raise ClassroomDenied(INVALID_CODE)
        row = session.get(ClassroomEntryCode, payload["code"], populate_existing=True)
        if (row is None or row.generation != payload["generation"] or row.class_id != payload["class"]
                or payload["binding"] != _binding(student)):
            raise ClassroomDenied(INVALID_CODE)
    else:
        row = session.scalar(select(ClassroomEntryCode).where(ClassroomEntryCode.program_id == program_id,
            ClassroomEntryCode.digest == _digest(program_id, code), ClassroomEntryCode.is_current.is_(True)))
    if row is None or row.program_id != program_id or not row.is_current:
        raise ClassroomDenied(INVALID_CODE)
    _active(session, program_id, row.class_id, enrollment=True)
    return row


def _binding(student):
    return hmac.new(_key("classroom-entry-account:v1"),
        f"{student.profile_id}:{student.session_version}:{student.credential_hash}".encode(), hashlib.sha256).hexdigest()


def _encode(payload):
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _parse_intent(token):
    try:
        if not isinstance(token, str) or len(token) > 1500 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[0-9a-f]{64}", token):
            raise ValueError()
        body, signature = token.split(".")
        if not hmac.compare_digest(signature, hmac.new(_key("classroom-entry-intent:v1"), body.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if (set(data) != {"program", "class", "code", "generation", "student", "binding", "iat", "exp"}
                or _encode(data) != body or any(type(data[k]) is not int for k in data if k != "binding")
                or not isinstance(data["binding"], str) or data["exp"] - data["iat"] != int(INTENT_LENGTH.total_seconds())
                or not data["iat"] <= int(_now().timestamp()) < data["exp"]):
            raise ValueError()
        return data
    except (ValueError, TypeError, UnicodeError) as error:
        raise ClassroomDenied(INVALID_CODE) from error


@_mutation
def prepare_entry(session, *, student, program_id, code):
    row = _entry(session, program_id, code=code)
    profile = _student(session, student, require_eligibility=False)
    now = int(_now().timestamp())
    body = _encode({"program": program_id, "class": row.class_id, "code": row.id,
        "generation": row.generation, "student": profile.id, "binding": _binding(student),
        "iat": now, "exp": now + int(INTENT_LENGTH.total_seconds())})
    return body + "." + hmac.new(_key("classroom-entry-intent:v1"), body.encode(), hashlib.sha256).hexdigest()


def _anchor(session, class_id, profile_id):
    return s1._fresh(session, ClassroomStudentMembership,
        ClassroomStudentMembership.class_id == class_id, ClassroomStudentMembership.profile_id == profile_id)


def _held(session, membership_id):
    return session.scalar(select(ClassroomMembershipHold).where(
        ClassroomMembershipHold.membership_id == membership_id, ClassroomMembershipHold.released_at.is_(None)))


def _periods(session, membership_id):
    # Writers already hold Program/Class/anchor locks. Replace cached ORM values
    # so history validation sees every persisted period, including closed ones.
    return list(session.scalars(select(ClassroomMembershipPeriod).where(
        ClassroomMembershipPeriod.membership_id == membership_id).order_by(ClassroomMembershipPeriod.starts_at)
        .execution_options(populate_existing=True)))


def _validated_periods(session, membership_id):
    periods = _periods(session, membership_id)
    # Existing corrupt overlapping history is a refusal, never silently repaired.
    for index, period in enumerate(periods):
        if index and (periods[index-1].ended_at is None or utc(periods[index-1].ended_at) > utc(period.starts_at)):
            raise ClassroomDenied("Membership history overlaps; operator review required.")
    return periods


def _open_period(session, anchor, now):
    periods = _validated_periods(session, anchor.id)
    if periods and periods[-1].ended_at is None:
        if periods[-1].state != "active" or utc(periods[-1].starts_at) > now:
            raise ClassroomDenied("Membership requires explicit reinstatement.")
        return False
    if periods and utc(periods[-1].ended_at) > now:
        raise ClassroomDenied("Membership period overlaps existing history.")
    session.add(ClassroomMembershipPeriod(membership_id=anchor.id, starts_at=now, state="active"))
    return True


def _close_period(session, anchor, now, reason):
    for period in _validated_periods(session, anchor.id):
        if period.ended_at is None:
            if now <= utc(period.starts_at):
                raise ClassroomDenied("Membership changed at this instant; retry shortly.")
            period.ended_at, period.ended_reason = now, reason
    session.flush()


@_mutation
def join_class(session, *, student, program_id, code=None, intent=None):
    row = _entry(session, program_id, code=code, intent=intent, student=student)
    profile = _student(session, student)
    # Eligibility locks can wait. Recheck time, code and all paid entry predicates
    # immediately before writing, including an intent expiring during that wait.
    row = _entry(session, program_id, code=code, intent=intent, student=student)
    anchor = _anchor(session, row.class_id, profile.id)
    if anchor is None:
        anchor = ClassroomStudentMembership(class_id=row.class_id, profile_id=profile.id)
        session.add(anchor)
        session.flush()
    if _held(session, anchor.id):
        raise ClassroomDenied("Membership requires explicit reinstatement.")
    if _open_period(session, anchor, _now()):
        _audit(session, program_id, "student_joined", student=profile.id, profile_id=profile.id,
               class_id=row.class_id, membership_id=anchor.id, code_id=row.id,
               entitlement_id=_paid(session, program_id).entitlement_id)
    return anchor


@_mutation
def leave_class(session, *, student, program_id, class_id):
    s1._lock_program(session, program_id)
    s1._class(session, program_id, class_id)
    profile = _student(session, student, require_eligibility=False)
    anchor = _anchor(session, class_id, profile.id)
    if anchor is None:
        raise ClassroomDenied("Membership unavailable.")
    periods = _validated_periods(session, anchor.id)
    if any(p.ended_at is None and p.state == "held" for p in periods):
        raise ClassroomDenied("Membership requires explicit reinstatement.")
    if any(p.ended_at is None for p in periods):
        _close_period(session, anchor, _now(), "departed")
        _audit(session, program_id, "student_left", student=profile.id, profile_id=profile.id,
               class_id=class_id, membership_id=anchor.id, reason="voluntary")
    return anchor


def _managed_member(session, actor, program_id, class_id, membership_id):
    program = s1._lock_program(session, program_id)
    actor_id = s1._authority(session, program, actor, admin=True)
    s1._class(session, program_id, class_id)
    anchor = s1._fresh(session, ClassroomStudentMembership, ClassroomStudentMembership.id == membership_id,
                       ClassroomStudentMembership.class_id == class_id)
    if anchor is None:
        raise ClassroomDenied("Membership unavailable in this Class.")
    return actor_id, anchor


@_mutation
def suspend_member(session, *, actor, program_id, class_id, membership_id, reason="conduct", remove=False):
    if reason not in {"conduct", "admin_removal"} or type(remove) is not bool:
        raise ClassroomDenied("Choose an explicit administrative reason.")
    actor_id, anchor = _managed_member(session, actor, program_id, class_id, membership_id)
    now = _now()
    _close_period(session, anchor, now, "changed")
    hold = session.get(ClassroomMembershipHold, anchor.id)
    if hold is None:
        hold = ClassroomMembershipHold(membership_id=anchor.id)
        session.add(hold)
    hold.state, hold.reason = "removed" if remove else "suspended", reason
    hold.held_by_verifier_id, hold.held_at = actor_id, now
    hold.released_at, hold.released_by_verifier_id = None, None
    _audit(session, program_id, "student_removed" if remove else "student_suspended", actor=actor_id,
           profile_id=anchor.profile_id, class_id=class_id, membership_id=anchor.id, reason=reason)
    return anchor


@_mutation
def reinstate_member(session, *, actor, program_id, class_id, membership_id):
    actor_id, anchor = _managed_member(session, actor, program_id, class_id, membership_id)
    _paid(session, program_id)
    _active(session, program_id, class_id)
    _eligible_profile(session, anchor.profile_id)
    _paid(session, program_id)
    hold = _held(session, anchor.id)
    periods = _validated_periods(session, anchor.id)
    if hold is None and not any(p.state == "held" and p.ended_at is None for p in periods):
        raise ClassroomDenied("No administrative hold to reinstate.")
    now = _now()
    _close_period(session, anchor, now, "changed")
    if hold:
        hold.released_at, hold.released_by_verifier_id = now, actor_id
    _open_period(session, anchor, now)
    _audit(session, program_id, "student_reinstated", actor=actor_id, profile_id=anchor.profile_id,
           class_id=class_id, membership_id=anchor.id, reason="explicit_reinstatement",
           entitlement_id=_paid(session, program_id).entitlement_id)
    return anchor


def check_teacher_addition(session, program_id, class_id, verifier_id):
    decision = _paid(session, program_id)
    _active(session, program_id, class_id)
    teachers = _teaching_adults(session, program_id)
    if verifier_id not in teachers and len(teachers) >= decision.teacher_limit:
        raise ClassroomDenied("Teaching director allowance reached.")


def assign_teacher(session, **kwargs):
    _require_enabled()
    return s1.assign_teacher(session, **kwargs)


def end_teaching(session, **kwargs):
    _require_enabled()
    return s1.end_teaching(session, **kwargs)


def contribution_active(session, *, program_id, class_id, profile_id):
    """S2 relationship status only. MUST NOT be used as reporting/game consent."""
    if not entitlement_decision(session, program_id).allowed or not eligible(session, profile_id):
        return False
    state = session.get(ClassroomClassState, class_id, populate_existing=True)
    if state is None or state.program_id != program_id or state.state != "active":
        return False
    anchor = session.scalar(select(ClassroomStudentMembership).where(
        ClassroomStudentMembership.class_id == class_id, ClassroomStudentMembership.profile_id == profile_id))
    if anchor is None or _held(session, anchor.id):
        return False
    return any(p.state == "active" and utc(p.starts_at) <= _now() and p.ended_at is None
               for p in _periods(session, anchor.id))
