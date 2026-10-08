"""Internal S3 authorization proof. No report fields or exports are approved.

Decisions contain sensitive scope identifiers and MUST NOT be serialized to a
browser. Callers own the transaction and must finish using a decision before
commit/rollback. PostgreSQL reads take the same Program-first locks as writers;
they linearize before or after withdrawal, never across it. No HTTP surface is
added while there is no approved student-data contract.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, timezone
from functools import wraps
import os
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from . import classroom as s1, classroom_s2 as s2
from .age_models import AccountPrivacy
from .age_privacy import utc
from .classroom_models import (
    ClassroomEntitlement, ClassroomMembershipPeriod, ClassroomReportingPeriod,
    ClassroomS2AuditEvent, ClassroomS3AuditEvent, ClassroomTeachingAssignment,
)
from .models import PracticeChart

SCOPE_VERSION = "classroom-boundary-v1"
NOTICE_VERSION = "classroom-boundary-notice-v1"
OPERATION = "history_boundary"
CENTRAL = ZoneInfo("America/Chicago")
UNAVAILABLE = "Classroom reporting unavailable."


def _now():
    return datetime.now(timezone.utc)


def enabled():
    return s2.enabled() and os.getenv("CLASSROOM_S3_ENABLED") == "1"


def _boundary(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        if not enabled():
            raise s1.ClassroomDenied(UNAVAILABLE)
        try:
            return function(session, *args, **kwargs)
        except s1.ClassroomDenied as error:
            # The same public-safe denial for absent and unauthorized scope.
            raise s1.ClassroomDenied(UNAVAILABLE) from error
    return s1._mutation(checked)


@dataclass(frozen=True)
class ReportingDecision:
    allowed: bool
    reason: str
    source_type: str
    program_id: int
    class_id: int
    profile_id: int
    period_id: int
    assignment_id: int
    starts_at: datetime
    ends_at: datetime
    event_key: tuple[int, int] | None = None
    # Never a reusable bearer capability: only useful in this live transaction.
    _session: object = field(default=None, repr=False, compare=False)
    _transaction: object = field(default=None, repr=False, compare=False)


def _exact_ids(*values):
    if any(type(value) is not int or value <= 0 for value in values):
        raise s1.ClassroomDenied(UNAVAILABLE)


def _scope(scope_version, notice_version):
    if scope_version != SCOPE_VERSION or notice_version != NOTICE_VERSION:
        raise s1.ClassroomDenied(UNAVAILABLE)


def _account_rule(session, profile_id):
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    # Existing child consent is for an exact named free director connection,
    # not a Class or its future co-directors. No positive Class path is inferred.
    if rule is None or rule.age_band not in {"13to17", "adult"} or rule.consent_id:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return rule


def _activation(session, state):
    instant = session.scalar(select(ClassroomS2AuditEvent.occurred_at).where(
        ClassroomS2AuditEvent.program_id == state.program_id,
        ClassroomS2AuditEvent.class_id == state.class_id,
        ClassroomS2AuditEvent.action.in_(("class_activated", "class_reactivated")))
        .order_by(ClassroomS2AuditEvent.occurred_at.desc(), ClassroomS2AuditEvent.id.desc()).limit(1))
    return utc(instant or state.created_at)


def _context(session, program_id, class_id, profile_id, *, student=None):
    _exact_ids(program_id, class_id, profile_id)
    s1._lock_program(session, program_id)
    state = s2._active(session, program_id, class_id)
    if student is None:
        s2._eligible_profile(session, profile_id)
    elif s2._student(session, student).id != profile_id:
        raise s1.ClassroomDenied(UNAVAILABLE)
    rule = _account_rule(session, profile_id)
    anchor = s2._anchor(session, class_id, profile_id)
    if anchor is None or s2._held(session, anchor.id):
        raise s1.ClassroomDenied(UNAVAILABLE)
    now = _now()  # time-sensitive checks follow all potentially blocking locks
    periods = s2._validated_periods(session, anchor.id)
    member = next((p for p in periods if p.state == "active" and p.ended_at is None
                   and utc(p.starts_at) <= now), None)
    entitlement = s2.entitlement_decision(session, program_id, at=now)
    if member is None or not entitlement.allowed:
        raise s1.ClassroomDenied(UNAVAILABLE)
    entitlement = session.get(ClassroomEntitlement, entitlement.entitlement_id, populate_existing=True)
    activation = _activation(session, state)
    if activation > now or utc(rule.declared_at) > now:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return anchor, member, entitlement, rule, activation, now


def _periods(session, membership_id):
    rows = list(session.scalars(select(ClassroomReportingPeriod).where(
        ClassroomReportingPeriod.membership_id == membership_id)
        .order_by(ClassroomReportingPeriod.starts_at, ClassroomReportingPeriod.id)
        .with_for_update().execution_options(populate_existing=True)))
    for previous, current in zip(rows, rows[1:]):
        if previous.ended_at is None or utc(previous.ended_at) > utc(current.starts_at):
            raise s1.ClassroomDenied(UNAVAILABLE)
    return rows


def _source_end(session, row, now):
    """Earliest retained source interruption, even after renewal/reactivation.

    S2 audit carries archive instants. Membership and entitlement records retain
    their original ends. This never reopens an older reporting interval.
    """
    member = session.get(ClassroomMembershipPeriod, row.membership_period_id, populate_existing=True)
    entitlement = session.get(ClassroomEntitlement, row.entitlement_id, populate_existing=True)
    watermark = (session.get(ClassroomS2AuditEvent, row.source_audit_id, populate_existing=True)
                 if row.source_audit_id is not None else None)
    if row.source_audit_id is not None and (
            watermark is None or watermark.program_id != row.program_id
            or utc(watermark.occurred_at) > utc(row.starts_at)):
        return utc(row.starts_at)
    if (member is None or member.membership_id != row.membership_id or member.state != "active"
            or entitlement is None or entitlement.program_id != row.program_id):
        return utc(row.starts_at)
    boundaries = [utc(entitlement.ends_at)]
    if member.ended_at is not None:
        boundaries.append(utc(member.ended_at))
    if entitlement.ended_at is not None:
        boundaries.append(utc(entitlement.ended_at))
    # Any package replacement may temporarily over-limit the Program even when
    # an old trial remains valid. Require explicit reauthorization afterward.
    changes = session.scalars(select(ClassroomS2AuditEvent.occurred_at).where(
        ClassroomS2AuditEvent.program_id == row.program_id,
        ClassroomS2AuditEvent.id > (row.source_audit_id or 0),
        ((ClassroomS2AuditEvent.class_id == row.class_id)
         & (ClassroomS2AuditEvent.action == "class_archived"))
        | ClassroomS2AuditEvent.action.in_(("institutional_created", "institutional_changed", "institutional_ended"))))
    for changed in changes:
        boundaries.append(utc(changed))
    return min(boundaries)


def _same_source(row, context):
    anchor, member, entitlement, rule, activation, now = context
    return (row.program_id == entitlement.program_id and row.class_id == anchor.class_id
            and row.membership_id == anchor.id and row.profile_id == anchor.profile_id
            and row.membership_period_id == member.id and row.entitlement_id == entitlement.id
            and utc(row.account_declared_at) == utc(rule.declared_at)
            and row.consent_id == rule.consent_id
            and utc(row.class_activation_at) == activation
            and row.scope_version == SCOPE_VERSION and row.notice_version == NOTICE_VERSION
            and utc(row.starts_at) <= now)


def _audit(session, row, action, now, reason=None):
    session.add(ClassroomS3AuditEvent(program_id=row.program_id, class_id=row.class_id,
        profile_id=row.profile_id, period_id=row.id, actor_profile_id=row.profile_id,
        action=action, scope_version=row.scope_version, notice_version=row.notice_version,
        reason=reason, occurred_at=now))


def _close(session, row, *, now, reason, effective_end=None):
    row.ended_at = max(utc(row.starts_at), min(now, effective_end or now))
    row.ended_by_profile_id, row.ended_reason = row.profile_id, reason
    _audit(session, row, "reporting_withdrawn", now, reason)
    session.flush()


def _grant(session, *, student, program_id, class_id, scope_version, notice_version, reauthorize):
    _scope(scope_version, notice_version)
    if type(student) is not s2.StudentAuthentication:
        raise s1.ClassroomDenied(UNAVAILABLE)
    context = _context(session, program_id, class_id, student.profile_id, student=student)
    anchor, member, entitlement, rule, activation, now = context
    periods = _periods(session, anchor.id)
    if bool(periods) != reauthorize:
        raise s1.ClassroomDenied(UNAVAILABLE)
    opened = next((row for row in periods if row.ended_at is None), None)
    if opened is not None:
        end = _source_end(session, opened, now)
        if _same_source(opened, context) and end > now:
            raise s1.ClassroomDenied(UNAVAILABLE)  # no duplicate grants
        _close(session, opened, now=now, reason="source_changed", effective_end=end)
    if periods and periods[-1].ended_at and utc(periods[-1].ended_at) > now:
        raise s1.ClassroomDenied(UNAVAILABLE)
    row = ClassroomReportingPeriod(program_id=program_id, class_id=class_id,
        membership_id=anchor.id, membership_period_id=member.id, profile_id=anchor.profile_id,
        authorizer_profile_id=anchor.profile_id, account_declared_at=rule.declared_at,
        consent_id=rule.consent_id, entitlement_id=entitlement.id,
        source_audit_id=session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(
            ClassroomS2AuditEvent.program_id == program_id)),
        class_activation_at=activation, scope_version=scope_version, notice_version=notice_version,
        starts_at=now, created_at=now)
    session.add(row)
    session.flush()
    _audit(session, row, "reporting_reauthorized" if reauthorize else "reporting_granted", now)
    return row


@_boundary
def grant_reporting(session, *, student, program_id, class_id, scope_version, notice_version):
    return _grant(session, student=student, program_id=program_id, class_id=class_id,
                  scope_version=scope_version, notice_version=notice_version, reauthorize=False)


@_boundary
def reauthorize_reporting(session, *, student, program_id, class_id, scope_version, notice_version):
    return _grant(session, student=student, program_id=program_id, class_id=class_id,
                  scope_version=scope_version, notice_version=notice_version, reauthorize=True)


@_boundary
def withdraw_reporting(session, *, student, program_id, class_id):
    _exact_ids(program_id, class_id)
    s1._lock_program(session, program_id)
    s1._class(session, program_id, class_id)
    # Withdrawal is available after entitlement loss/archive/child withdrawal.
    profile = s2._student(session, student, require_eligibility=False)
    anchor = s2._anchor(session, class_id, profile.id)
    if anchor is None:
        raise s1.ClassroomDenied(UNAVAILABLE)
    row = next((r for r in _periods(session, anchor.id) if r.ended_at is None), None)
    if row is not None:
        now = _now()
        _close(session, row, now=now, reason="withdrawn", effective_end=_source_end(session, row, now))
    return row


def _authorize(session, *, actor, program_id, class_id, profile_id, operation, fields):
    # There is no approved data-field or export contract in S3. Fail before
    # querying any protected record. Connected Students is never consulted.
    if operation != OPERATION or fields != ():
        raise s1.ClassroomDenied(UNAVAILABLE)
    context = _context(session, program_id, class_id, profile_id)
    anchor, member, entitlement, rule, activation, now = context
    actor_id = s1._actor(session, actor)
    teacher = session.scalar(select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.verifier_id == actor_id,
        ClassroomTeachingAssignment.starts_at <= now,
        ClassroomTeachingAssignment.ended_at.is_(None))
        .execution_options(populate_existing=True))
    row = next((r for r in _periods(session, anchor.id) if r.ended_at is None), None)
    if teacher is None or row is None or not _same_source(row, context):
        raise s1.ClassroomDenied(UNAVAILABLE)
    end = _source_end(session, row, now)
    if end <= now:
        raise s1.ClassroomDenied(UNAVAILABLE)
    start = max(utc(row.starts_at), utc(member.starts_at), utc(teacher.starts_at),
                utc(entitlement.starts_at), utc(entitlement.created_at), activation,
                utc(rule.declared_at), utc(rule.public_from or rule.declared_at))
    return ReportingDecision(True, "authorized_boundary_only", "classroom_reporting",
        program_id, class_id, profile_id, row.id, teacher.id, start, min(now, end),
        _session=session, _transaction=session.get_transaction())


@_boundary
def authorize_reporting(session, *, actor, program_id, class_id, profile_id,
                        operation=OPERATION, fields=()):
    return _authorize(session, actor=actor, program_id=program_id, class_id=class_id,
                      profile_id=profile_id, operation=operation, fields=fields)


@_boundary
def chart_history_decision(session, *, actor, program_id, class_id, profile_id, chart_id):
    decision = _authorize(session, actor=actor, program_id=program_id, class_id=class_id,
                          profile_id=profile_id, operation=OPERATION, fields=())
    _exact_ids(chart_id)
    # Select only occurrence/submission evidence; no note, parent, metric,
    # verification, reward or unrelated student's record enters this service.
    chart = session.execute(select(PracticeChart.practice_date, PracticeChart.created_at).where(
        PracticeChart.id == chart_id, PracticeChart.profile_id == profile_id)).one_or_none()
    allowed = False
    if chart is not None:
        day_start = datetime.combine(chart.practice_date, time.min, CENTRAL).astimezone(timezone.utc)
        day_end = datetime.combine(chart.practice_date + timedelta(days=1), time.min, CENTRAL).astimezone(timezone.utc)
        # The entire occurrence day must be within the authorized interval.
        # Submission time is an additional bound, never an occurrence substitute.
        allowed = (decision.starts_at <= day_start and day_end <= decision.ends_at
                   and decision.starts_at <= utc(chart.created_at) <= decision.ends_at)
    return replace(decision, allowed=allowed,
                   reason="history_in_interval" if allowed else "history_unavailable",
                   event_key=(profile_id, chart_id))


def deduplicate_history_decisions(session, decisions, *, actor):
    """Internal proof composition; no data, total, or zero for withheld events.

    Only already-authorized Class decisions in this transaction may contribute.
    Free relationship results are deliberately not an accepted input. The exact
    Program remains part of the key: A can never turn B's denial into approval.
    """
    output = {}
    for decision in decisions:
        if (type(decision) is not ReportingDecision or decision._session is not session
                or decision._transaction is not session.get_transaction()
                or decision._transaction is None or not decision._transaction.is_active
                or decision.event_key is None):
            raise s1.ClassroomDenied(UNAVAILABLE)
        # Earlier checks may have preceded a withdrawal in THIS transaction.
        # Decisions are evidence, never transferable authority: authenticate
        # and evaluate every exact relationship again before combining.
        decision = chart_history_decision(session, actor=actor,
            program_id=decision.program_id, class_id=decision.class_id,
            profile_id=decision.profile_id, chart_id=decision.event_key[1])
        key = (decision.program_id, *decision.event_key)
        if key not in output or decision.allowed:
            output[key] = decision
    return tuple(output.values())
