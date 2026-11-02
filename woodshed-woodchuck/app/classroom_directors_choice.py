"""Saved Class assignments. No reporting, student activity, or reward authority.

All reads and writes lock Program -> Class -> authentication before loading work.
Current teachers manage Class-owned work; provenance retains each author role.
"""
from datetime import date, datetime, time, timedelta, timezone
from functools import wraps
import os
import re
from zoneinfo import ZoneInfo

from sqlalchemy import select
from . import classroom as s1, classroom_s2 as s2, classroom_enrollment as enrollment
from .classroom_capabilities import capability_decision
from .classroom_enrollment_views import _class_names
from .classroom_models import ClassroomTeachingAssignment
from .classroom_choice_models import ClassroomChoiceAssignment as Assignment, ClassroomChoiceVersion as Version
from .age_privacy import utc
from .weekly_homework import exercise_spec, weekly_instrument
from .models import TrustedVerifier

CENTRAL = ZoneInfo('America/Chicago')
PAGE_SIZE = 20
UNAVAILABLE = 'Director’s Choice unavailable. Refresh Class access and try again.'


def enabled():
    return enrollment.enabled() and os.getenv('CLASSROOM_DIRECTORS_CHOICE_ENABLED') == '1'


def _operation(function):
    @wraps(function)
    def checked(session, *args, **kwargs):
        if not enabled():
            raise s1.ClassroomDenied(UNAVAILABLE)
        return function(session, *args, **kwargs)
    return s2._mutation(checked)


def week(value=None):
    if value is None:
        today = s2._now().astimezone(CENTRAL).date()
        return today - timedelta(days=today.weekday())
    try:
        result = date.fromisoformat(value) if type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else value
        if type(result) is not date or result.weekday() != 0 or not 2000 <= result.year <= 9998:
            raise ValueError()
        return result
    except ValueError as error:
        raise s1.ClassroomDenied('Choose a Monday for the Central-time assignment week.') from error


def bounds(start):
    return tuple(datetime.combine(day, time.min, CENTRAL).astimezone(timezone.utc)
                 for day in (start, start + timedelta(days=7)))


def _scope(session, program_id, class_id):
    enrollment._ids(program_id, class_id)
    s1._lock_program(session, program_id)
    s2._active(session, program_id, class_id)
    s2._paid(session, program_id)


def _director(session, actor, program_id, class_id):
    _scope(session, program_id, class_id)
    verifier_id = s1._actor(session, actor)
    rows = list(session.scalars(select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.program_id == program_id,
        ClassroomTeachingAssignment.class_id == class_id,
        ClassroomTeachingAssignment.verifier_id == verifier_id,
        ClassroomTeachingAssignment.ended_at.is_(None)).execution_options(populate_existing=True)))
    if len(rows) != 1 or utc(rows[0].starts_at) > s2._now():
        raise s1.ClassroomDenied(UNAVAILABLE)
    return rows[0]


def _student(session, student, program_id, class_id):
    _scope(session, program_id, class_id)
    profile = s2._student(session, student)
    decision = capability_decision(session, profile.id, 'note-names', program_id=program_id, class_id=class_id)
    if not any(source.allowed and source.source_type == 'classroom'
               and source.program_id == program_id and source.class_id == class_id for source in decision.sources):
        raise s1.ClassroomDenied(UNAVAILABLE)


def content(start, fields):
    if type(fields) is not dict:
        raise s1.ClassroomDenied('Choose an existing recipe or enter short practice instructions.')
    if fields.get('kind') == 'custom' and set(fields) == {'kind', 'title', 'instructions'}:
        output = {'kind': 'custom'}
        for name, maximum in (('title', 120), ('instructions', 1000)):
            value = fields[name]
            if type(value) is str:
                value = value.replace('\r\n', '\n').replace('\r', '\n')
            if (type(value) is not str or not 1 <= len(value.strip()) <= maximum
                    or any(ord(c) < 32 and c not in '\n\t' for c in value)):
                raise s1.ClassroomDenied('Use a title up to 120 characters and instructions up to 1,000 characters.')
            output[name] = value.strip()
        output['instructions'] = [output['instructions']]
        return output
    if fields.get('kind') == 'recipe' and set(fields) == {'kind', 'instrument', 'focus', 'mode'}:
        try:
            if any(type(value) is not str for value in fields.values()):
                raise ValueError()
            instrument = weekly_instrument(fields['instrument'])
            spec = exercise_spec(start, fields['focus'], instrument, fields['mode'])
        except (ValueError, TypeError) as error:
            raise s1.ClassroomDenied('Choose a supported Homework recipe, Focus and Game Mode.') from error
        return {**spec, 'kind': 'recipe', 'instrument': instrument, 'focus': fields['focus'], 'mode': fields['mode']}
    raise s1.ClassroomDenied('Unsupported assignment content.')


def _owned(session, program_id, class_id, assignment_id):
    enrollment._ids(assignment_id)
    row = s1._fresh(session, Assignment, Assignment.id == assignment_id,
        Assignment.program_id == program_id, Assignment.class_id == class_id)
    if row is None:
        raise s1.ClassroomDenied(UNAVAILABLE)
    return row


def _latest(session, row):
    version = session.get(Version, (row.id, row.current_version), populate_existing=True)
    if version is None or (version.program_id, version.class_id) != (row.program_id, row.class_id):
        raise s1.ClassroomDenied(UNAVAILABLE)
    return version


def _project(row, version):
    now = s2._now()
    start, end = bounds(row.week_start)
    return {'id': row.id, 'week': row.week_start.isoformat(),
        'through': (row.week_start + timedelta(days=6)).isoformat(), 'version': version.version,
        'authored_at': utc(version.authored_at).astimezone(CENTRAL).isoformat(),
        'state': version.state, 'timing': 'expired' if now >= end else 'scheduled' if now < start else 'current',
        'editable': now < end and version.state == 'active', 'content': version.content}


@_operation
def create(session, *, actor, program_id, class_id, week_start, request_key, fields):
    teacher = _director(session, actor, program_id, class_id)
    start = week(week_start)
    if start < week():
        raise s1.ClassroomDenied('Past weeks are read-only.')
    if type(request_key) is not str or not re.fullmatch('[0-9a-f]{32}', request_key):
        raise s1.ClassroomDenied('Reopen the new assignment form.')
    saved = content(start, fields)
    row = session.scalar(select(Assignment).where(Assignment.class_id == class_id, Assignment.request_key == request_key))
    if row:
        original = session.get(Version, (row.id, 1))
        if (row.week_start != start or original.content != saved or original.author_assignment_id != teacher.id):
            raise s1.ClassroomDenied('This form was already used. Reopen the Class.')
        return _project(row, _latest(session, row))
    row = Assignment(program_id=program_id, class_id=class_id, week_start=start,
                     created_at=s2._now(), request_key=request_key, current_version=1)
    session.add(row)
    session.flush()
    version = Version(assignment_id=row.id, version=1, program_id=program_id, class_id=class_id,
        author_assignment_id=teacher.id, authored_at=s2._now(), state='active', content=saved)
    session.add(version)
    return _project(row, version)


@_operation
def change(session, *, actor, program_id, class_id, assignment_id, expected_version, fields=None, cancel=False):
    teacher = _director(session, actor, program_id, class_id)
    row = _owned(session, program_id, class_id, assignment_id)
    previous = _latest(session, row)
    if (type(expected_version) is not int or expected_version != row.current_version
            or type(cancel) is not bool or not _project(row, previous)['editable']):
        raise s1.ClassroomDenied('This assignment changed, expired, or was cancelled. Reopen the Class.')
    if cancel and fields is not None:
        raise s1.ClassroomDenied('Cancellation cannot replace instructions.')
    saved = previous.content if cancel else content(row.week_start, fields)
    version = Version(assignment_id=row.id, version=row.current_version + 1,
        program_id=program_id, class_id=class_id, author_assignment_id=teacher.id,
        authored_at=s2._now(), state='cancelled' if cancel else 'active', content=saved)
    session.add(version)
    row.current_version += 1
    return _project(row, version)


def _page(session, program_id, class_id, week_start, before):
    start = week(week_start)
    query = select(Assignment).where(Assignment.program_id == program_id,
        Assignment.class_id == class_id, Assignment.week_start == start)
    if before is not None:
        enrollment._ids(before)
        query = query.where(Assignment.id < before)
    rows = list(session.scalars(query.order_by(Assignment.id.desc()).limit(PAGE_SIZE + 1)))
    return {**_class_names(session, program_id, class_id), 'week': start.isoformat(),
        'through': (start + timedelta(days=6)).isoformat(), 'can_create': start >= week(),
        'assignments': [_project(row, _latest(session, row)) for row in rows[:PAGE_SIZE]],
        'next_before': rows[PAGE_SIZE - 1].id if len(rows) > PAGE_SIZE else None}


@_operation
def director_class(session, *, actor, program_id, class_id):
    _director(session, actor, program_id, class_id)
    return _class_names(session, program_id, class_id)


@_operation
def director_page(session, *, actor, program_id, class_id, week_start=None, before=None):
    _director(session, actor, program_id, class_id)
    return _page(session, program_id, class_id, week_start, before)


@_operation
def student_page(session, *, student, program_id, class_id, week_start=None, before=None):
    _student(session, student, program_id, class_id)
    return _page(session, program_id, class_id, week_start, before)


@_operation
def history(session, *, actor, program_id, class_id, assignment_id, before=None):
    _director(session, actor, program_id, class_id)
    row = _owned(session, program_id, class_id, assignment_id)
    query = select(Version).where(Version.assignment_id == row.id)
    if before is not None:
        enrollment._ids(before)
        query = query.where(Version.version < before)
    rows = list(session.scalars(query.order_by(Version.version.desc()).limit(PAGE_SIZE + 1)))
    authors = dict(session.execute(select(ClassroomTeachingAssignment.id, TrustedVerifier.display_name).join(
        TrustedVerifier, TrustedVerifier.id == ClassroomTeachingAssignment.verifier_id).where(
        ClassroomTeachingAssignment.id.in_([row.author_assignment_id for row in rows]))).all())
    return {**_class_names(session, program_id, class_id), 'assignment_id': row.id,
        'week': row.week_start.isoformat(), 'versions': [
            {**_project(row, version), 'author_assignment_id': version.author_assignment_id,
             'author_name': authors[version.author_assignment_id]} for version in rows[:PAGE_SIZE]],
        'next_before': rows[PAGE_SIZE - 1].version if len(rows) > PAGE_SIZE else None}
