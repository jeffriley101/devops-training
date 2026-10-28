"""Student choices for future educational reporting; no statistics reader.

Consent is not a bearer capability. Every future reader must recheck this exact
boundary in its own transaction before using an independently approved dataset.
Individual P-Chart sharing and S3 boundary-only grants never consult these rows.
"""
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import os

from sqlalchemy import func, select

from . import classroom as s1, classroom_s2 as s2, classroom_reporting as boundary
from .age_privacy import utc
from .classroom_models import (
    ClassroomProgressAuthorization as Authorization, ClassroomProgressRecipient as Recipient,
    ClassroomAuditEvent, ClassroomS2AuditEvent, ClassroomTeachingAssignment, ClassroomEntitlement,
    ClassroomClassState,
)

SCOPE_VERSION = "classroom-educational-reporting-v1"
NOTICE_VERSION = "classroom-student-reporting-notice-v1"
LABEL = "Share my Classroom progress with my Band Director"
NOTICE = (
    "I authorize the Band Director(s) listed for this Class to receive available educational statistics "
    "from my authorized reporting period: practice activity and statistics, my Student Practice Rating, "
    "Verified and Pristine practice, Homework and weekly progress, Arcade and skill-building results, "
    "and other supported educational analytics. I can withdraw at any time to stop future comprehensive "
    "reporting access. My personal records and Classroom learning benefits will remain available. "
    "Individual P-Charts are shared only through their separate sharing checkbox."
)
NOTICE_DIGEST = hashlib.sha256((LABEL + "\n" + NOTICE).encode()).hexdigest()
UNAVAILABLE = "Classroom reporting authorization unavailable."


def enabled():
    return boundary.enabled() and os.getenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED") == "1"


def _operation(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        if not enabled():
            raise s1.ClassroomDenied(UNAVAILABLE)
        try:
            return function(session, *args, **kwargs)
        except s1.ClassroomDenied as error:
            raise s1.ClassroomDenied(UNAVAILABLE) from error
    return s1._mutation(checked)


def _teachers(session, program_id, class_id, now):
    return list(session.scalars(select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.starts_at <= now,
        ClassroomTeachingAssignment.ended_at.is_(None))
        .order_by(ClassroomTeachingAssignment.id).execution_options(populate_existing=True)))


def _open(session, program_id, class_id, profile_id):
    return session.scalar(select(Authorization).where(
        Authorization.program_id == program_id, Authorization.class_id == class_id,
        Authorization.profile_id == profile_id, Authorization.withdrawn_at.is_(None))
        .with_for_update().execution_options(populate_existing=True))


def _latest(session, program_id, class_id, profile_id):
    return session.scalar(select(Authorization).where(
        Authorization.program_id == program_id, Authorization.class_id == class_id,
        Authorization.profile_id == profile_id).order_by(Authorization.id.desc()).limit(1)
        .execution_options(populate_existing=True))


def _revision(row):
    return (row.id, utc(row.withdrawn_at).isoformat() if row.withdrawn_at else None) if row else (None, None)


def _entitlement_transition(session, program_id, now):
    """Latest effective scheduled source change, including unobserved intervals.

    The Program lock freezes these schedules. Reuse S2's selector at each
    crossed start/end; fully shadowed or cancelled packages do not interrupt
    the selected source. For unchanged schedules this marker only advances.
    Audited schedule edits are covered separately by the source revision.
    """
    rows = list(session.scalars(select(ClassroomEntitlement).where(
        ClassroomEntitlement.program_id == program_id, ClassroomEntitlement.status == "active")
        .order_by(ClassroomEntitlement.id.desc()).execution_options(populate_existing=True)))
    instants = sorted({utc(instant) for row in rows for instant in (row.starts_at, row.ends_at)
                       if utc(instant) <= now})
    previous, changed_at = None, None
    for instant in instants:
        selected = s2._select_entitlement(rows, instant)
        identity = selected.id if selected is not None else None
        if identity != previous:
            previous, changed_at = identity, instant
    return changed_at.isoformat() if changed_at is not None else None


def _capacity_revision(session, entitlement):
    """Retained Program history for shared eligibility, under the Program lock.

    All-ever Class/teacher counts bound usage at every prior instant. If both
    bounds fit, other Classes cannot have caused an over-limit interruption.
    Once a bound exceeds the allowance, conservatively bind the relevant audit
    maxima: the retained rows do not record complete historical usage snapshots.
    Never filter these bounds to current active/open rows; recovery would then
    erase the interruption. Supported writers retain the rows, so each bound
    and included watermark only advances for an unchanged entitlement.
    """
    program_id = entitlement.program_id
    classes = session.scalar(select(func.count()).select_from(ClassroomClassState).where(
        ClassroomClassState.program_id == program_id))
    teachers = session.scalar(select(func.count(func.distinct(ClassroomTeachingAssignment.verifier_id))).where(
        ClassroomTeachingAssignment.program_id == program_id))
    teacher_history_needed = teachers > entitlement.teacher_limit
    assignments = session.scalar(select(func.max(ClassroomAuditEvent.id)).where(
        ClassroomAuditEvent.program_id == program_id,
        ClassroomAuditEvent.action.in_(("teacher_assigned", "teacher_revoked", "teacher_departed")))) \
        if teacher_history_needed else 0
    # Class activity changes both the active-Class count and which teachers
    # consume the shared allowance. Include archived Classes' retained history.
    activity = session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(
        ClassroomS2AuditEvent.program_id == program_id,
        ClassroomS2AuditEvent.action.in_(("class_activated", "class_archived", "class_reactivated",
                                        "teacher_activated", "teacher_deactivated")))) \
        if teacher_history_needed or classes > entitlement.class_limit else 0
    return assignments or 0, activity or 0


def _source_revision(session, context):
    """Revision of this student's sources, read while holding the Program lock.

    S1/S2 writers append these retained audit IDs atomically with each source
    change under the same lock. Their scoped high-water marks never return to
    an older value when a change is reversed, including at identical timestamps.
    Other Classes also affect shared capacity. Exclude their changes only when
    retained history proves usage could not exceed the selected allowances.
    Other students, join codes and administrative roles remain excluded.
    """
    anchor, member, entitlement, rule, activation, now = context
    assignments = session.scalar(select(func.max(ClassroomAuditEvent.id)).where(
        ClassroomAuditEvent.program_id == entitlement.program_id,
        ClassroomAuditEvent.class_id == anchor.class_id,
        ClassroomAuditEvent.action.in_(("teacher_assigned", "teacher_revoked", "teacher_departed")))) or 0
    sources = session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(
        ClassroomS2AuditEvent.program_id == entitlement.program_id,
        ClassroomS2AuditEvent.action.in_(("trial_started", "institutional_created",
            "institutional_changed", "institutional_ended"))
        | ((ClassroomS2AuditEvent.class_id == anchor.class_id)
           & ClassroomS2AuditEvent.action.in_(("class_activated", "class_archived", "class_reactivated",
                                              "teacher_activated", "teacher_deactivated")))
        | ((ClassroomS2AuditEvent.class_id == anchor.class_id)
           & (ClassroomS2AuditEvent.profile_id == anchor.profile_id)
           & ClassroomS2AuditEvent.action.in_(("student_joined", "student_left", "student_suspended",
                                              "student_removed", "student_reinstated"))))) or 0
    # v1/v2 cannot prove absence of scheduled/shared-capacity interruptions.
    # Retain their evidence; require a freshly reviewed v3 form and acceptance.
    snapshot = ("classroom-progress-source-v3", entitlement.program_id, anchor.class_id, anchor.profile_id,
        assignments, sources, anchor.id, member.id, entitlement.id,
        utc(rule.declared_at).isoformat(), activation.isoformat(), NOTICE_DIGEST,
        _entitlement_transition(session, entitlement.program_id, now),
        _capacity_revision(session, entitlement))
    return hashlib.sha256(json.dumps(snapshot, separators=(",", ":")).encode()).hexdigest()


def _current(session, row, context, teachers):
    anchor, member, entitlement, rule, activation, now = context
    recipients = set(session.scalars(select(Recipient.assignment_id).where(
        Recipient.authorization_id == row.id, Recipient.class_id == row.class_id,
        Recipient.program_id == row.program_id))) if row else set()
    return bool(row and teachers and row.withdrawn_at is None
        and row.profile_id == row.accepted_by_profile_id == anchor.profile_id
        and row.class_id == anchor.class_id and row.program_id == entitlement.program_id
        and row.membership_id == anchor.id and row.membership_period_id == member.id
        and row.entitlement_id == entitlement.id and utc(row.account_declared_at) == utc(rule.declared_at)
        and utc(row.class_activation_at) == activation
        and row.scope_version == SCOPE_VERSION and row.notice_version == NOTICE_VERSION
        and row.notice_digest == NOTICE_DIGEST and utc(row.accepted_at) <= now
        and row.source_revision is not None and row.source_revision == _source_revision(session, context)
        and recipients == {teacher.id for teacher in teachers}
        and boundary._source_end(session, row, now) > now)


def _withdraw(session, row, now, reason):
    row.withdrawn_at = max(now, utc(row.accepted_at))
    row.withdrawn_by_profile_id, row.withdrawal_reason = row.profile_id, reason
    session.flush()


@_operation
def accept(session, *, student, program_id, class_id, notice_version, opted_in, assignment_ids, previous,
           source_revision):
    if opted_in is not True or notice_version != NOTICE_VERSION or type(student) is not s2.StudentAuthentication:
        raise s1.ClassroomDenied(UNAVAILABLE)
    context = boundary._context(session, program_id, class_id, student.profile_id, student=student)
    anchor, member, entitlement, rule, activation, now = context
    # Check before idempotency: a stale form may never be used as new consent,
    # even when current source values or the recipient set have returned.
    if not isinstance(source_revision, str) or source_revision != _source_revision(session, context):
        raise s1.ClassroomDenied(UNAVAILABLE)
    teachers = _teachers(session, program_id, class_id, now)
    if (not teachers or type(assignment_ids) is not tuple
            or any(type(i) is not int for i in assignment_ids)
            or assignment_ids != tuple(t.id for t in teachers)):
        raise s1.ClassroomDenied(UNAVAILABLE)
    opened = _open(session, program_id, class_id, student.profile_id)
    if _current(session, opened, context, teachers):
        return opened  # Repeat submissions retain the original acceptance evidence.
    latest = _latest(session, program_id, class_id, student.profile_id)
    # A delayed POST must not reverse a later withdrawal, and clock reversal
    # must not create overlapping acceptance periods.
    if (previous != _revision(latest) or latest is not None
            and utc(latest.withdrawn_at or latest.accepted_at) > now):
        raise s1.ClassroomDenied(UNAVAILABLE)
    if opened is not None:
        _withdraw(session, opened, now, "replaced")
    row = Authorization(program_id=program_id, class_id=class_id, profile_id=anchor.profile_id,
        membership_id=anchor.id, membership_period_id=member.id, entitlement_id=entitlement.id,
        account_declared_at=rule.declared_at, class_activation_at=activation,
        source_audit_id=session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(
            ClassroomS2AuditEvent.program_id == program_id)),
        scope_version=SCOPE_VERSION, notice_version=NOTICE_VERSION, notice_digest=NOTICE_DIGEST,
        accepted_at=now, accepted_by_profile_id=anchor.profile_id,
        acceptance_session_version=student.session_version, source_revision=source_revision)
    session.add(row)
    session.flush()
    session.add_all([Recipient(authorization_id=row.id, assignment_id=t.id,
        class_id=class_id, program_id=program_id) for t in teachers])
    session.flush()
    return row


@_operation
def withdraw(session, *, student, program_id, class_id):
    boundary._exact_ids(program_id, class_id)
    s1._lock_program(session, program_id)
    s1._class(session, program_id, class_id)
    profile = s2._student(session, student, require_eligibility=False)
    row = _open(session, program_id, class_id, profile.id)
    if row is not None:
        _withdraw(session, row, boundary._now(), "withdrawn")
    return row


@dataclass(frozen=True)
class Choice:
    eligible: bool
    authorized: bool
    saved: bool
    assignment_ids: tuple[int, ...] = ()
    revision: tuple[int | None, str | None] = (None, None)
    source_revision: str | None = None


@_operation
def choice(session, *, student, program_id, class_id):
    """Own preference only; still permits withdrawal after source/account restrictions."""
    boundary._exact_ids(program_id, class_id)
    s1._lock_program(session, program_id)
    s1._class(session, program_id, class_id)
    profile = s2._student(session, student, require_eligibility=False)
    row = _open(session, program_id, class_id, profile.id)
    revision = _revision(_latest(session, program_id, class_id, profile.id))
    try:
        context = boundary._context(session, program_id, class_id, profile.id, student=student)
        teachers = _teachers(session, program_id, class_id, context[-1])
    except s1.ClassroomDenied:
        return Choice(False, False, row is not None, revision=revision)
    return Choice(bool(teachers), _current(session, row, context, teachers), row is not None,
                  tuple(t.id for t in teachers), revision, _source_revision(session, context))


@_operation
def authorize_boundary(session, *, actor, program_id, class_id, profile_id,
                       operation="authorization_boundary", fields=()):
    """Internal, transaction-bound evidence only. No data fields or exports approved."""
    if operation != "authorization_boundary" or fields != ():
        raise s1.ClassroomDenied(UNAVAILABLE)
    context = boundary._context(session, program_id, class_id, profile_id)
    teachers = _teachers(session, program_id, class_id, context[-1])
    actor_id = s1._actor(session, actor)
    teacher = next((t for t in teachers if t.verifier_id == actor_id), None)
    row = _open(session, program_id, class_id, profile_id)
    if teacher is None or not _current(session, row, context, teachers):
        raise s1.ClassroomDenied(UNAVAILABLE)
    _, member, entitlement, rule, activation, now = context
    start = max(utc(row.accepted_at), utc(member.starts_at), utc(teacher.starts_at),
                utc(entitlement.starts_at), utc(entitlement.created_at), activation,
                utc(rule.declared_at), utc(rule.public_from or rule.declared_at))
    return boundary.ReportingDecision(True, "authorized_boundary_only", SCOPE_VERSION,
        program_id, class_id, profile_id, row.id, teacher.id, start, now,
        _session=session, _transaction=session.get_transaction())
