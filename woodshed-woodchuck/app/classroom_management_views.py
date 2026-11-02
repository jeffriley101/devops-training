"""Scoped management projections; no practice, reporting or public-name reader."""
from datetime import datetime, timezone

from sqlalchemy import select

from . import classroom as s1, classroom_s2 as s2
from .age_privacy import sharing_allowed, utc
from .classroom_enrollment_views import _membership
from .classroom_models import (ClassroomProgram, ClassroomRoleGrant, ClassroomClass,
    ClassroomClassState, ClassroomTeachingAssignment, ClassroomOwnershipTransfer,
    ClassroomEntitlement, ClassroomEntryCode, ClassroomStudentMembership)
from .models import Organization, TrustedVerifier, WoodchuckProfile

PAGE_SIZE = 20


def _time(value):
    return utc(value).strftime('%b %d, %Y · %H:%M UTC') if value else None


def _adult(session, ident):
    row = session.get(TrustedVerifier, ident, populate_existing=True)
    if row is None:
        raise s1.ClassroomDenied('Adult unavailable.')
    return {'id': row.id, 'name': row.display_name}


def _incoming(session, actor_id, program_id=None):
    query = select(ClassroomOwnershipTransfer).join(ClassroomProgram,
        ClassroomProgram.organization_id == ClassroomOwnershipTransfer.program_id).where(
        ClassroomOwnershipTransfer.recipient_verifier_id == actor_id,
        ClassroomOwnershipTransfer.status == 'pending',
        ClassroomOwnershipTransfer.owner_version == ClassroomProgram.owner_version,
        ClassroomOwnershipTransfer.initiator_verifier_id == ClassroomProgram.owner_verifier_id)
    if program_id is not None:
        query = query.where(ClassroomOwnershipTransfer.program_id == program_id)
    return query


def catalog(session, *, actor, before=None):
    actor_id = s1._actor(session, actor)
    programs = {item['program_id']: item for item in s1.my_classes(session, actor=actor)['programs']}
    incoming = _incoming(session, actor_id)
    if before is not None:
        incoming = incoming.where(ClassroomOwnershipTransfer.program_id < before)
    for row in session.scalars(incoming.order_by(ClassroomOwnershipTransfer.program_id.desc()).limit(PAGE_SIZE + 1)):
        if row.program_id not in programs:
            programs[row.program_id] = {'program_id': row.program_id,
                'display_name': session.get(Organization, row.program_id).name,
                'roles': [], 'owner': False, 'classes': [], 'incoming_only': True}
        programs[row.program_id]['incoming'] = True
    rows = sorted((item for item in programs.values() if before is None or item['program_id'] < before),
        key=lambda item: item['program_id'], reverse=True)
    return {'programs': [{**item, 'classes': item['classes'][:5], 'more_classes': len(item['classes']) > 5}
                        for item in rows[:PAGE_SIZE]],
        'next_before': rows[PAGE_SIZE - 1]['program_id'] if len(rows) > PAGE_SIZE else None}


def scope(session, *, actor, program_id, class_id=None):
    """One Program lock and the accepted metadata authority, freshly resolved."""
    program = s1._lock_program(session, program_id)
    actor_id = s1._actor(session, actor)
    incoming = session.scalar(_incoming(session, actor_id, program_id))
    try:
        metadata = s1.my_classes(session, actor=actor, program_id=program_id, class_id=class_id)['programs'][0]
    except s1.ClassroomDenied:
        if incoming is None or class_id is not None:
            raise
        metadata = {'program_id': program_id, 'display_name': session.get(Organization, program_id).name,
            'owner': False, 'roles': [], 'classes': [], 'incoming_only': True}
    roles = set(metadata['roles'])
    manager = metadata['owner'] or 'admin' in roles
    return {'program': program, 'actor_id': actor_id, 'incoming': incoming, 'metadata': metadata,
        'manage': manager, 'delegate': metadata['owner'],
        'codes': metadata['owner'] or bool({'admin', 'code_manager'} & roles),
        'trial': metadata['owner'] or 'head_director' in roles}


def _page_rows(session, query, model, before=None):
    if before is not None:
        query = query.where(model.id < before)
    rows = list(session.scalars(query.order_by(model.id.desc()).limit(PAGE_SIZE + 1)))
    return rows[:PAGE_SIZE], rows[PAGE_SIZE - 1].id if len(rows) > PAGE_SIZE else None


def _entitlement(session, program_id):
    decision = s2.entitlement_decision(session, program_id)
    selected = session.get(ClassroomEntitlement, decision.entitlement_id) if decision.entitlement_id else None
    # No internal approval fingerprint or case reference is projected.
    history = list(session.scalars(select(ClassroomEntitlement).where(
        ClassroomEntitlement.program_id == program_id).order_by(ClassroomEntitlement.id.desc()).limit(PAGE_SIZE + 1)))
    used_trial = session.scalar(select(ClassroomEntitlement.id).where(
        ClassroomEntitlement.program_id == program_id, ClassroomEntitlement.source == 'trial')) is not None
    classes, teachers = s2.usage(session, program_id)
    return {'allowed': decision.allowed, 'reason': decision.reason, 'source': decision.source,
        'starts': _time(selected.starts_at) if selected else None, 'ends': _time(selected.ends_at) if selected else None,
        'class_limit': decision.class_limit, 'teacher_limit': decision.teacher_limit,
        'classes_used': classes, 'teachers_used': teachers, 'trial_used': used_trial,
        'history': [{'source': row.source, 'status': row.status, 'starts': _time(row.starts_at),
            'ends': _time(row.ends_at), 'class_limit': row.class_limit, 'teacher_limit': row.teacher_limit}
            for row in history[:PAGE_SIZE]], 'more_history': len(history) > PAGE_SIZE}


def _class_summary(session, item):
    state = session.get(ClassroomClassState, item['id'], populate_existing=True)
    return {**item, 'state': state.state if state else 'not activated',
        'enrollment_open': bool(state and state.enrollment_open)}


@s2._mutation
def detail(session, *, actor, program_id, class_id=None, before=None, section='classes'):
    current = scope(session, actor=actor, program_id=program_id, class_id=class_id)
    program, meta = current['program'], current['metadata']
    result = {'program_id': program_id, 'program_name': meta['display_name'],
        'owner': _adult(session, program.owner_verifier_id), 'owner_version': program.owner_version,
        'roles': meta['roles'], 'is_owner': meta['owner'], 'manage': current['manage'],
        'delegate': current['delegate'], 'codes': current['codes'], 'trial': current['trial'],
        'incoming_only': meta.get('incoming_only', False), 'selected_class': None,
        'proposal': None, 'entitlement': None, 'classes': [], 'grants': []}
    proposal = current['incoming']
    if current['delegate']:
        proposal = session.scalar(select(ClassroomOwnershipTransfer).where(
            ClassroomOwnershipTransfer.program_id == program_id, ClassroomOwnershipTransfer.status == 'pending',
            ClassroomOwnershipTransfer.owner_version == program.owner_version,
            ClassroomOwnershipTransfer.initiator_verifier_id == program.owner_verifier_id))
    if proposal:
        result['proposal'] = {'id': proposal.id, 'recipient': _adult(session, proposal.recipient_verifier_id),
            'can_accept': proposal.recipient_verifier_id == current['actor_id'], 'can_cancel': current['delegate']}
    if result['incoming_only']:
        return result
    result['entitlement'] = _entitlement(session, program_id)
    if class_id is None:
        classes = sorted((item for item in meta['classes'] if section != 'classes' or before is None or item['id'] < before),
            key=lambda item: item['id'], reverse=True)
        result['classes'] = [_class_summary(session, item) for item in classes[:PAGE_SIZE]]
        result['next_class'] = classes[PAGE_SIZE - 1]['id'] if len(classes) > PAGE_SIZE else None
        if current['delegate']:
            query = select(ClassroomRoleGrant).where(ClassroomRoleGrant.program_id == program_id,
                ClassroomRoleGrant.starts_at <= datetime.now(timezone.utc), ClassroomRoleGrant.ended_at.is_(None))
            grants, result['next_role'] = _page_rows(session, query, ClassroomRoleGrant,
                before if section == 'roles' else None)
            result['grants'] = [{'id': grant.id, 'role': grant.role, 'adult': _adult(session, grant.verifier_id)} for grant in grants]
        return result
    room = _class_summary(session, meta['classes'][0])
    query = select(ClassroomTeachingAssignment).where(ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.starts_at <= datetime.now(timezone.utc), ClassroomTeachingAssignment.ended_at.is_(None))
    assignments, next_teacher = _page_rows(session, query, ClassroomTeachingAssignment,
        before if section == 'teachers' else None)
    own_assignment = session.scalar(query.where(ClassroomTeachingAssignment.verifier_id == current['actor_id']))
    room['assignments'] = [{'id': row.id, 'adult': _adult(session, row.verifier_id),
        'can_end': current['manage'], 'can_depart': row.verifier_id == current['actor_id']} for row in assignments]
    room['next_teacher'] = next_teacher
    room['teaching'] = own_assignment is not None
    code = session.scalar(select(ClassroomEntryCode).where(ClassroomEntryCode.program_id == program_id,
        ClassroomEntryCode.class_id == class_id, ClassroomEntryCode.is_current.is_(True)))
    room['code'] = {'generation': code.generation, 'issued': _time(code.issued_at)} if code else None
    room['roster'], room['next_roster'] = roster(session, current=current, class_id=class_id,
        before=before if section == 'roster' else None)
    s1._actor(session, actor)  # Credentials may change while account locks wait.
    result['selected_class'] = room
    return result


def roster(session, *, current, class_id, before=None):
    """Leadership's narrow WC#/instrument/status roster; never a practice reader.

    Owner/Admin can manage retained membership references. A current teacher sees
    identity only for current, unheld members in an active entitled Class. Existing
    account/child restrictions fail closed. Public display names are never used as
    school names, and no name collection or new consent authority is introduced.
    """
    program_id = current['program'].organization_id
    room = s2._class_state(session, program_id, class_id)
    available = bool(room and room.state == 'active' and s2.entitlement_decision(session, program_id).allowed)
    if not current['manage'] and not available:
        return [], None
    query = select(ClassroomStudentMembership).where(ClassroomStudentMembership.class_id == class_id)
    anchors, next_before = _page_rows(session, query, ClassroomStudentMembership, before)
    from . import classroom_identity
    members = {anchor.id: _membership(session, program_id, class_id, anchor.profile_id) for anchor in anchors}
    profiles = {}
    if available and classroom_identity.enabled():
        # Programs can share accounts with different enrollment order. Lock page
        # subjects in global profile order, not local membership/cursor order.
        for anchor in sorted(anchors, key=lambda item: item.profile_id):
            if current['manage'] or members[anchor.id]['active']:
                try:
                    profiles[anchor.profile_id] = s2._eligible_profile(session, anchor.profile_id, require_eligibility=False)
                except s1.ClassroomDenied:
                    profiles[anchor.profile_id] = None
        # A lock wait may cross the entitlement's ending instant.
        available = bool(room and room.state == 'active' and s2.entitlement_decision(session, program_id).allowed)
        if not current['manage'] and not available:
            return [], None
    items = []
    for anchor in anchors:
        member = members[anchor.id]
        show_identity = available and (current['manage'] or member['active'])
        if show_identity and classroom_identity.enabled():
            profile = profiles.get(anchor.profile_id)
        else:
            profile = session.get(WoodchuckProfile, anchor.profile_id, populate_existing=True) if show_identity else None
        show_identity = bool(profile and profile.status == 'active' and sharing_allowed(session, profile.id))
        # Unassigned roles cannot reach this Class projection. Former/held student
        # identities remain hidden from teachers while management keeps references.
        if not current['manage'] and not member['active']:
            continue
        private = classroom_identity._roster_identity(session, program_id=program_id, profile_id=anchor.profile_id) if show_identity else {'school_name': None, 'identity_version': 0}
        items.append({'id': anchor.id, 'woodchuck_id': profile.woodchuck_id if show_identity else None,
            **private, 'can_edit_identity': bool(show_identity and member['active'] and classroom_identity.enabled()),
            'instrument': profile.instrument if show_identity else None, 'status': member['label'],
            'can_suspend': current['manage'] and member['active'],
            'can_remove': current['manage'] and member['label'] != 'Removed',
            'can_reinstate': current['manage'] and member['label'] in {'Suspended', 'Removed', 'On hold'}})
    return items, next_before
