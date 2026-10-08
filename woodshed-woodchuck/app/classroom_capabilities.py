"""Internal, source-aware student decisions; never adult data authority.

This read-only resolver describes existing account/Full access and the five S3
game policies. It creates no Full seat, verifier relationship, saved progress,
reward, or reporting permission. Callers must authenticate the student separately
and must not serialize the scoped source evidence into public responses.
"""
from dataclasses import dataclass, replace
import os

from sqlalchemy import select

from . import classroom_s2 as s2
from .age_privacy import eligible, utc
from .classroom_models import (
    ClassroomClass, ClassroomClassState, ClassroomMembershipHold,
    ClassroomStudentMembership,
)
from .memberships import membership_is_active
from .models import Membership, MembershipSeat, TesterEnrollment, WoodchuckProfile
from .tester_enrollments import LIFETIME_TESTER_COHORTS


ORDINARY_CAPABILITIES = frozenset({"pristine_use", "personal_progress_save"})
FULL_CAPABILITIES = ORDINARY_CAPABILITIES | {"full_access", "normal_arcade_waiver", "practice_insights"}


@dataclass(frozen=True)
class SourceDecision:
    allowed: bool
    reason: str
    source_type: str
    profile_id: int | None = None
    source_id: int | None = None
    seat_id: int | None = None
    program_id: int | None = None
    class_id: int | None = None
    membership_id: int | None = None
    membership_period_id: int | None = None
    entitlement_id: int | None = None


@dataclass(frozen=True)
class CapabilityDecision:
    capability: str
    allowed: bool
    reason: str
    sources: tuple[SourceDecision, ...]


def enabled():
    return s2.enabled() and os.getenv("CLASSROOM_S3_ENABLED") == "1"


def _classroom_sources(session, profile_id, now, program_id, class_id):
    if not enabled():
        return [(SourceDecision(False, "classroom_disabled", "classroom", profile_id), frozenset())]
    from .arcade_access import CLASSROOM
    query = select(ClassroomStudentMembership, ClassroomClass).join(
        ClassroomClass, ClassroomClass.id == ClassroomStudentMembership.class_id
    ).where(ClassroomStudentMembership.profile_id == profile_id)
    if program_id is not None:
        query = query.where(ClassroomClass.program_id == program_id)
    if class_id is not None:
        query = query.where(ClassroomClass.id == class_id)
    output = []
    for anchor, klass in session.execute(query.order_by(ClassroomStudentMembership.id)
                                        .execution_options(populate_existing=True)):
        source = SourceDecision(False, "class_inactive", "classroom", profile_id,
                                program_id=klass.program_id, class_id=klass.id,
                                membership_id=anchor.id)
        state = session.get(ClassroomClassState, klass.id, populate_existing=True)
        if state is None or state.program_id != klass.program_id or state.state != "active":
            output.append((source, frozenset()))
            continue
        entitlement = s2.entitlement_decision(session, klass.program_id, at=now)
        source = replace(source, entitlement_id=entitlement.entitlement_id)
        if not entitlement.allowed:
            output.append((replace(source, reason="entitlement_" + entitlement.reason), frozenset()))
            continue
        hold = session.scalar(select(ClassroomMembershipHold).where(
            ClassroomMembershipHold.membership_id == anchor.id,
            ClassroomMembershipHold.released_at.is_(None)
        ).execution_options(populate_existing=True))
        if hold is not None:
            output.append((replace(source, reason="membership_held"), frozenset()))
            continue
        try:
            periods = s2._validated_periods(session, anchor.id)
        except s2.ClassroomDenied:
            output.append((replace(source, reason="membership_history_invalid"), frozenset()))
            continue
        period = next((row for row in periods if row.state == "active"
                       and row.ended_at is None and utc(row.starts_at) <= now), None)
        if period is None:
            output.append((replace(source, reason="membership_inactive"), frozenset()))
            continue
        output.append((replace(source, allowed=True, reason="active_classroom_source",
                               membership_period_id=period.id),
                       ORDINARY_CAPABILITIES | frozenset(CLASSROOM)))
    return output or [(SourceDecision(False, "classroom_source_missing", "classroom", profile_id,
                                    program_id=program_id, class_id=class_id), frozenset())]


def _sources(session, profile_id, now, program_id, class_id):
    # Explicit refresh avoids treating an ORM identity-map account as current.
    profile = session.get(WoodchuckProfile, profile_id, populate_existing=True)
    if profile is None or not eligible(session, profile_id):
        return [(SourceDecision(False, "account_ineligible", "ordinary", profile_id), frozenset())]
    result = [(SourceDecision(True, "eligible_account", "ordinary", profile_id), ORDINARY_CAPABILITIES)]
    personal = session.execute(select(Membership, MembershipSeat).join(MembershipSeat).where(
        MembershipSeat.profile_id == profile_id, MembershipSeat.removed_at.is_(None)
    ).execution_options(populate_existing=True))
    for membership, seat in personal:
        active = membership_is_active(membership, now)
        result.append((SourceDecision(active, "active_personal_full" if active else "personal_full_inactive",
                                      "personal_full", profile_id, source_id=membership.id,
                                      seat_id=seat.id), FULL_CAPABILITIES))
    for enrollment in session.scalars(select(TesterEnrollment).where(
        TesterEnrollment.profile_id == profile_id,
        TesterEnrollment.cohort_key.in_(LIFETIME_TESTER_COHORTS)
    ).order_by(TesterEnrollment.id).execution_options(populate_existing=True)):
        active = utc(enrollment.joined_at) <= now
        result.append((SourceDecision(active, "active_tester_lifetime" if active else "tester_not_started",
                                      "tester_lifetime", profile_id, source_id=enrollment.id), FULL_CAPABILITIES))
    result.extend(_classroom_sources(session, profile_id, now, program_id, class_id))
    return result


def _decision(capability, sources):
    decisions = tuple(replace(source, allowed=False, reason="not_granted_by_source")
                      if source.allowed and capability not in grants else source
                      for source, grants in sources)
    allowed = any(source.allowed for source in decisions)
    return CapabilityDecision(capability, allowed,
                              "permitted_source" if allowed else "no_permitted_source", decisions)


def resolve_student_capabilities(session, profile_id, *, program_id=None, class_id=None, at=None):
    """Union only expressly granted capabilities; optional Classroom scope is exact.

    These are current feature-policy decisions, not historical reporting grants.
    An exact Class scope requires its Program, so Program A cannot rescue B.
    Reporting authorization is intentionally absent from the capability set.
    """
    from .arcade_access import CLASSROOM
    capabilities = FULL_CAPABILITIES | frozenset(CLASSROOM)
    valid_ids = (type(profile_id) is int and profile_id > 0
                 and (program_id is None or type(program_id) is int and program_id > 0)
                 and (class_id is None or type(class_id) is int and class_id > 0 and program_id is not None))
    if not valid_ids:
        denied = SourceDecision(False, "registered_account_and_exact_scope_required", "ordinary")
        return {key: CapabilityDecision(key, False, denied.reason, (denied,)) for key in capabilities}
    sources = _sources(session, profile_id, utc(at or s2._now()), program_id, class_id)
    return {key: _decision(key, sources) for key in sorted(capabilities)}


def capability_decision(session, profile_id, capability, *, program_id=None, class_id=None, at=None):
    """Unsupported operations (including all reports/exports) fail closed."""
    from .arcade_access import CLASSROOM
    if capability not in FULL_CAPABILITIES | frozenset(CLASSROOM):
        return CapabilityDecision(capability, False, "capability_not_approved", ())
    return resolve_student_capabilities(session, profile_id, program_id=program_id,
                                        class_id=class_id, at=at)[capability]
