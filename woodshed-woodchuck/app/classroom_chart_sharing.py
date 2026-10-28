"""Explicit individual-chart permissions, separate from Classroom reporting."""
from types import SimpleNamespace

from sqlalchemy import func, select

from . import classroom, classroom_director, classroom_reporting as reporting, classroom_s2 as s2
from .age_privacy import utc
from . import classroom_chart_eligibility as eligibility
from .classroom_models import (ClassroomChartDirectorShare, ClassroomClass, ClassroomS2AuditEvent,
    ClassroomStudentMembership, ClassroomTeachingAssignment, ClassroomStudentInvitation, ClassroomClassState)
from .models import PracticeChart, TrustedVerifier, WoodchuckProfile

UNAVAILABLE = "Classroom sharing unavailable."


def _enabled(session):
    if not classroom_director.enabled():
        raise classroom.ClassroomDenied(UNAVAILABLE)
    connection = session.connection()
    if connection.dialect.name not in {"sqlite", "postgresql"}:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    if connection.dialect.name == "postgresql" and connection.get_isolation_level() != "READ COMMITTED":
        raise classroom.ClassroomDenied(UNAVAILABLE)
    if connection.dialect.name == "sqlite" and connection.exec_driver_sql("PRAGMA foreign_keys").scalar() != 1:
        raise classroom.ClassroomDenied(UNAVAILABLE)


def choices(session, student):
    _enabled(session)
    if type(student) is not s2.StudentAuthentication:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    rows = session.execute(select(ClassroomClass.program_id, ClassroomClass.id,
                                  ClassroomTeachingAssignment.id)
        .join(ClassroomStudentMembership, ClassroomStudentMembership.class_id == ClassroomClass.id)
        .join(ClassroomTeachingAssignment, ClassroomTeachingAssignment.class_id == ClassroomClass.id)
        .where(ClassroomStudentMembership.profile_id == student.profile_id,
               ClassroomTeachingAssignment.ended_at.is_(None))
        .order_by(ClassroomClass.program_id, ClassroomClass.id, ClassroomTeachingAssignment.id)).all()
    invited = session.execute(select(ClassroomStudentInvitation.program_id,
        ClassroomStudentInvitation.class_id, ClassroomStudentInvitation.assignment_id).where(
        ClassroomStudentInvitation.profile_id == student.profile_id,
        ClassroomStudentInvitation.accepted_at.is_not(None),
        ClassroomStudentInvitation.revoked_at.is_(None))).all()
    s2._student(session, student)
    reporting._account_rule(session, student.profile_id)
    result = []
    for program_id, class_id, assignment_id in sorted(set(rows + invited)):
        try:
            anchor = s2._anchor(session, class_id, student.profile_id)
            if anchor is not None:
                s2._validated_periods(session, anchor.id)
                if s2._held(session, anchor.id):
                    continue
            assignment = session.get(ClassroomTeachingAssignment, assignment_id, populate_existing=True)
            now = reporting._now()
            if assignment is None or utc(assignment.starts_at) > now or assignment.ended_at is not None:
                continue
            if not s2.contribution_active(session, program_id=program_id, class_id=class_id,
                                          profile_id=student.profile_id):
                state = session.get(ClassroomClassState, class_id, populate_existing=True)
                if (state is None or state.state != "active" or state.program_id != program_id
                        or not s2.entitlement_decision(session, program_id, at=now).allowed):
                    continue
                candidates = session.scalars(select(ClassroomStudentInvitation).where(
                    ClassroomStudentInvitation.assignment_id == assignment_id,
                    ClassroomStudentInvitation.profile_id == student.profile_id)
                    .execution_options(populate_existing=True))
                if not any(eligibility.invitation_matches(row, assignment, student.profile_id, now) for row in candidates):
                    continue
        except classroom.ClassroomDenied:
            continue
        room = session.get(ClassroomClass, class_id)
        verifier = session.get(TrustedVerifier, assignment.verifier_id)
        result.append({"program_id": program_id, "class_id": class_id,
                       "assignment_id": assignment_id, "class_name": room.display_name,
                       "director_name": verifier.display_name})
    return result


def lock_recipient(session, student, assignment_id):
    """Revalidate one exact recipient under the Program-first lock."""
    _enabled(session)
    if type(student) is not s2.StudentAuthentication:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    assignment = session.get(ClassroomTeachingAssignment, assignment_id, populate_existing=True)
    if assignment is None:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    try:
        return eligibility.enrolled_recipient(session, student=student, program_id=assignment.program_id,
                                  class_id=assignment.class_id, assignment_id=assignment.id)
    except classroom.ClassroomDenied:
        context, invitation = eligibility.invited_context(session, assignment_id=assignment.id,
            profile_id=student.profile_id, student=student)
        return eligibility.EnrolledRecipient(assignment.program_id, assignment.class_id, student.profile_id,
            None, assignment.id, assignment.verifier_id, context[2].id, invitation.id)


def _current_source(session, share, *, profile_id, assignment, student=None):
    """Recheck the saved source only; never substitute today's eligibility."""
    if share.class_id != assignment.class_id:
        return False
    if share.invitation_id is not None:
        if share.membership_period_id is not None:
            return False
        context, invitation = eligibility.invited_context(session, assignment_id=assignment.id,
            profile_id=profile_id, student=student, invitation_id=share.invitation_id)
        anchor, member, entitlement, rule, activation, now = context
        source_valid = eligibility.invitation_source_current(session, share, invitation, now)
    else:
        anchor, member, entitlement, rule, activation, now = reporting._context(
            session, assignment.program_id, assignment.class_id, profile_id, student=student)
        source = SimpleNamespace(program_id=assignment.program_id, class_id=assignment.class_id,
            membership_id=anchor.id, membership_period_id=share.membership_period_id,
            entitlement_id=share.entitlement_id, source_audit_id=share.source_audit_id,
            starts_at=share.selected_at)
        source_valid = member.id == share.membership_period_id and reporting._source_end(session, source, now) > now
    # Refresh after locks: a share withdrawal may have committed while waiting.
    session.refresh(share)
    session.refresh(assignment)
    return (share.withdrawn_at is None and utc(share.selected_at) <= now
        and assignment.ended_at is None and utc(assignment.starts_at) <= now
        and entitlement.id == share.entitlement_id and utc(rule.declared_at) == utc(share.account_declared_at)
        and activation == utc(share.class_activation_at) and source_valid)


def selection_current(session, *, student, share_id):
    """Check a displayed student choice under the same current source rules."""
    _enabled(session)
    share = session.get(ClassroomChartDirectorShare, share_id, populate_existing=True)
    if share is None or share.withdrawn_at is not None:
        return False
    if type(student) is not s2.StudentAuthentication:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    chart = session.scalar(select(PracticeChart.id).where(PracticeChart.id == share.chart_id,
        PracticeChart.profile_id == student.profile_id))
    assignment = session.get(ClassroomTeachingAssignment, share.assignment_id, populate_existing=True)
    if chart is None or assignment is None:
        return False
    return _current_source(session, share, profile_id=student.profile_id, assignment=assignment, student=student)


def set_sharing(session, *, student, chart_id, assignment_ids):
    """Replace only this student's active selections, within caller transaction."""
    _enabled(session)
    if type(chart_id) is not int or chart_id <= 0 or not isinstance(assignment_ids, list) or len(assignment_ids) > 1:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    if any(type(x) is not int or x <= 0 for x in assignment_ids) or len(set(assignment_ids)) != len(assignment_ids):
        raise classroom.ClassroomDenied(UNAVAILABLE)
    if type(student) is not s2.StudentAuthentication:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    # Inspect identifiers only before locking. Each mutation stays in one Program.
    open_refs = session.execute(select(ClassroomChartDirectorShare.id, ClassroomClass.program_id)
        .join(ClassroomClass, ClassroomClass.id == ClassroomChartDirectorShare.class_id)
        .join(PracticeChart, PracticeChart.id == ClassroomChartDirectorShare.chart_id)
        .where(PracticeChart.id == chart_id, PracticeChart.profile_id == student.profile_id,
               ClassroomChartDirectorShare.withdrawn_at.is_(None))).all()
    target = session.get(ClassroomTeachingAssignment, assignment_ids[0], populate_existing=True) if assignment_ids else None
    if assignment_ids and target is None:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    programs = {row.program_id for row in open_refs}
    if target is not None:
        programs.add(target.program_id)
    if len(programs) > 1:
        raise classroom.ClassroomDenied("Remove the previous sharing choice before choosing another Program.")
    if programs:
        classroom._lock_program(session, next(iter(programs)))
    if target is not None:
        decision = lock_recipient(session, student, target.id)
    profile = s2._student(session, student)
    chart = session.scalar(select(PracticeChart).where(PracticeChart.id == chart_id,
        PracticeChart.profile_id == profile.id).with_for_update().execution_options(populate_existing=True))
    if chart is None:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    open_rows = list(session.scalars(select(ClassroomChartDirectorShare).where(
        ClassroomChartDirectorShare.chart_id == chart_id,
        ClassroomChartDirectorShare.withdrawn_at.is_(None)).with_for_update()))
    if any(session.get(ClassroomClass, row.class_id).program_id not in programs for row in open_rows):
        raise classroom.ClassroomDenied("Sharing changed; reload and retry.")
    now = reporting._now()
    desired = set(assignment_ids)
    for row in open_rows:
        # A deliberate edit captures fresh source periods; it must never revive
        # a share made during a previous enrollment or entitlement.
        row.withdrawn_at = max(now, utc(row.selected_at))
    session.flush()
    for assignment_id in desired:
        if decision.invitation_id is None:
            context = reporting._context(session, target.program_id, target.class_id, profile.id, student=student)
        else:
            context, _ = eligibility.invited_context(session, assignment_id=target.id,
                profile_id=profile.id, student=student, invitation_id=decision.invitation_id)
        anchor, member, entitlement, rule, activation, now = context
        session.add(ClassroomChartDirectorShare(chart_id=chart.id, class_id=target.class_id,
            assignment_id=assignment_id, membership_period_id=member.id if member else None,
            invitation_id=decision.invitation_id, entitlement_id=entitlement.id,
            account_declared_at=rule.declared_at, class_activation_at=activation,
            source_audit_id=session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(
                ClassroomS2AuditEvent.program_id == target.program_id)), selected_at=now))
    session.flush()


def _shared_chart_records(session, *, actor, program_id, class_id):
    """Internal, transaction-local records after current exact-source checks.

    Both the original chart view and V3 analytics use this boundary. These
    records are not a roster or a reusable reporting authorization.
    """
    _enabled(session)
    classroom._lock_program(session, program_id)
    adult_id = classroom._actor(session, actor)
    assignments = list(session.scalars(select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.verifier_id == adult_id,
        ClassroomTeachingAssignment.ended_at.is_(None),
        ClassroomTeachingAssignment.starts_at <= reporting._now())))
    if len(assignments) != 1:
        raise classroom.ClassroomDenied(UNAVAILABLE)
    assignment = assignments[0]
    rows = session.scalars(select(ClassroomChartDirectorShare).where(
        ClassroomChartDirectorShare.class_id == class_id,
        ClassroomChartDirectorShare.assignment_id == assignment.id,
        ClassroomChartDirectorShare.withdrawn_at.is_(None))).all()
    output = []
    for share in rows:
        try:
            profile_id = session.scalar(select(PracticeChart.profile_id).where(PracticeChart.id == share.chart_id))
            if profile_id is None:
                continue
            if not _current_source(session, share, profile_id=profile_id, assignment=assignment):
                continue
            # Recheck actor after lock waits; the returned values contain no other charts.
            if classroom._actor(session, actor) != adult_id:
                raise classroom.ClassroomDenied(UNAVAILABLE)
            chart = session.get(PracticeChart, share.chart_id, populate_existing=True)
            profile = session.get(WoodchuckProfile, profile_id, populate_existing=True)
            if chart is not None and chart.profile_id == profile_id and profile is not None:
                output.append((profile_id, profile.display_name, chart))
        except classroom.ClassroomDenied:
            continue
    return output


def shared_charts(session, *, actor, program_id, class_id):
    """The existing individual-chart projection, with its original fields."""
    return [{"student": name, "date": chart.practice_date.isoformat(),
             "minutes": chart.minutes, "instrument": chart.instrument,
             "details": chart.practice_details or [], "note": chart.note or ""}
            for _, name, chart in _shared_chart_records(
                session, actor=actor, program_id=program_id, class_id=class_id)]
