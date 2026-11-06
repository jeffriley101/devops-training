"""Narrow enrollment projections. No roster, practice or reporting reader."""
from sqlalchemy import select

from . import classroom as s1, classroom_s2 as s2, classroom_enrollment as enrollment
from .age_privacy import utc
from .classroom_enrollment_models import ClassroomEnrollmentInvitation as Invitation
from .classroom_models import ClassroomClass, ClassroomClassState, ClassroomStudentMembership, ClassroomTeachingAssignment
from .models import Organization, TrustedVerifier, WoodchuckProfile

PAGE_SIZE = 20


def _membership(session, program_id, class_id, profile_id):
    anchor = s2._anchor(session, class_id, profile_id)
    if anchor is None:
        return {'label': 'Not enrolled', 'active': False, 'access': False}
    hold = s2._held(session, anchor.id)
    periods = s2._validated_periods(session, anchor.id)
    if hold:
        label = 'Removed' if hold.state == 'removed' else 'Suspended'
    elif any(p.ended_at is None and p.state == 'held' for p in periods):
        label = 'On hold'
    else:
        label = 'Member' if any(p.ended_at is None and p.state == 'active'
            and utc(p.starts_at) <= s2._now() for p in periods) else 'Not enrolled'
    return {'label': label, 'active': label == 'Member',
        'access': label == 'Member' and s2.contribution_active(session,
            program_id=program_id, class_id=class_id, profile_id=profile_id)}


def _class_names(session, program_id, class_id):
    room = s1._class(session, program_id, class_id)
    program = session.get(Organization, program_id)
    return {'program_id': program_id, 'class_id': class_id,
            'program_name': program.name, 'class_name': room.display_name}


@enrollment._operation
def code_review(session, *, student, program_id, code=None, intent=None):
    if code is not None:
        intent = s2.prepare_entry(session, student=student, program_id=program_id, code=code)
    row = s2._entry(session, program_id, intent=intent, student=student)
    profile = s2._student(session, student, require_eligibility=False)
    # Account locks may wait: the original code/intent authority is revalidated.
    row = s2._entry(session, program_id, intent=intent, student=student)
    member = _membership(session, program_id, row.class_id, profile.id)
    account_ready = s2.eligible(session, profile.id)
    return {**_class_names(session, program_id, row.class_id), 'intent': intent,
        'account': profile.woodchuck_id, 'account_ready': account_ready,
        'can_join': account_ready and member['label'] not in {'Suspended', 'Removed', 'On hold'},
        'membership': member}


@enrollment._operation
def student_membership(session, *, student, program_id, class_id):
    s1._lock_program(session, program_id)
    s1._class(session, program_id, class_id)
    profile = s2._student(session, student, require_eligibility=False)
    if s2._anchor(session, class_id, profile.id) is None:
        raise s1.ClassroomDenied(enrollment.UNAVAILABLE)
    return {**_class_names(session, program_id, class_id),
            'membership': _membership(session, program_id, class_id, profile.id)}


@enrollment._operation
def student_offer(session, *, student, invitation_id):
    row = enrollment.student_invitation(session, student=student, invitation_id=invitation_id)
    state = enrollment.status(row)
    member = _membership(session, row.program_id, row.class_id, row.profile_id)
    account_ready = s2.eligible(session, row.profile_id)
    assignment = session.get(ClassroomTeachingAssignment, row.assignment_id, populate_existing=True)
    room_state = session.get(ClassroomClassState, row.class_id, populate_existing=True)
    can_accept = bool(state in {'pending', 'deferred'} and account_ready and assignment
        and assignment.verifier_id == row.issued_by_verifier_id and assignment.ended_at is None
        and utc(assignment.starts_at) <= s2._now() and room_state and room_state.state == 'active'
        and room_state.enrollment_open and s2.entitlement_decision(session, row.program_id).allowed
        and member['label'] not in {'Suspended', 'Removed', 'On hold'})
    issuer = session.get(TrustedVerifier, row.issued_by_verifier_id)
    return {**_class_names(session, row.program_id, row.class_id), 'id': row.id, 'state': state,
        'director_name': issuer.display_name, 'membership': member, 'can_accept': can_accept,
        'account_ready': account_ready, 'expires': utc(row.expires_at).strftime('%b %d, %Y · %H:%M UTC')}


def director_classes(session, *, actor):
    verifier_id = s1._actor(session, actor)
    rows = session.execute(select(ClassroomClass, Organization.name).join(
        ClassroomTeachingAssignment, ClassroomTeachingAssignment.class_id == ClassroomClass.id).join(
        Organization, Organization.id == ClassroomClass.program_id).where(
        ClassroomTeachingAssignment.verifier_id == verifier_id,
        ClassroomTeachingAssignment.ended_at.is_(None),
        ClassroomTeachingAssignment.starts_at <= s2._now()).order_by(
        Organization.name, ClassroomClass.display_name).limit(100)).all()
    return [{'program_id': room.program_id, 'class_id': room.id,
        'program_name': name, 'class_name': room.display_name} for room, name in rows]


@enrollment._operation
def director_class(session, *, actor, program_id, class_id, before=None):
    s1._lock_program(session, program_id)
    from .classroom_school_authorization import require_disclosure
    require_disclosure(session, program_id)
    room = s1._class(session, program_id, class_id)
    verifier_id = s1._actor(session, actor)
    assignment = session.scalar(select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.verifier_id == verifier_id,
        ClassroomTeachingAssignment.starts_at <= s2._now(),
        ClassroomTeachingAssignment.ended_at.is_(None)).execution_options(populate_existing=True))
    query = select(Invitation).where(Invitation.program_id == program_id,
        Invitation.class_id == class_id, Invitation.issued_by_verifier_id == verifier_id)
    # Former issuers retain reference-only cleanup; never WC# or new Class data.
    if assignment is None and session.scalar(query.limit(1)) is None:
        raise s1.ClassroomDenied(enrollment.UNAVAILABLE)
    state = session.get(ClassroomClassState, class_id, populate_existing=True)
    access = s2.entitlement_decision(session, program_id).allowed
    show_targets = bool(assignment and state and state.state == 'active' and access)
    if before is not None:
        query = query.where(Invitation.id < before)
    offers = list(session.scalars(query.order_by(Invitation.id.desc()).limit(PAGE_SIZE + 1)))
    profiles = {}
    if show_targets:
        # Retained invitations do not preserve account permission. Recheck it
        # under the existing global account-lock order before projecting WC#s,
        # including when a denied issue request redisplays this list.
        for profile_id in sorted({row.profile_id for row in offers[:PAGE_SIZE]}):
            try:
                profiles[profile_id] = s2._eligible_profile(session, profile_id)
            except s1.ClassroomDenied:
                pass
        s1._actor(session, actor)
        show_targets = show_targets and s2.entitlement_decision(session, program_id).allowed
    require_disclosure(session, program_id)
    items = []
    for row in offers[:PAGE_SIZE]:
        profile = profiles.get(row.profile_id) if show_targets else None
        items.append({'id': row.id, 'state': enrollment.status(row),
            'woodchuck_id': profile.woodchuck_id if profile and profile.status == 'active' else None,
            'expires': utc(row.expires_at).strftime('%b %d, %Y · %H:%M UTC'),
            'can_revoke': enrollment.status(row) in {'pending', 'deferred'}})
    names = _class_names(session, program_id, class_id) if assignment else {
        'program_id': program_id, 'class_id': class_id, 'program_name': None, 'class_name': None}
    return {**names, 'offers': items, 'current_teacher': assignment is not None,
        'can_issue': bool(show_targets and state.enrollment_open),
        'enrollment_open': bool(state and state.enrollment_open), 'has_access': access,
        'next_before': offers[PAGE_SIZE - 1].id if len(offers) > PAGE_SIZE else None}


def account_return_path(request, profile):
    """Fixed first-party continuation only. No automatic join or acceptance."""
    if not enrollment.enabled():
        return None
    saved = request.session.get('classroom_enrollment_return')
    if not isinstance(saved, dict) or saved.get('account') != profile.woodchuck_id:
        return None
    if saved.get('kind') == 'invitations':
        return '/classroom/enrollment/invitations'
    if saved.get('kind') == 'code':
        try:
            data = s2._parse_intent(saved.get('intent'))
        except s1.ClassroomDenied:
            return None
        if data['student'] == profile.id:
            return '/classroom/enrollment/resume'
    return None
