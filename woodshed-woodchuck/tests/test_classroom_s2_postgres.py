"""S2 races on disposable PostgreSQL with observed pg_blocking_pids waits.

Every race uses S1's ordered_wait observer: transaction two must be blocked by
transaction one before release. Merely running threads concurrently is no proof.
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import classroom, classroom_s2 as service
from app.age_models import AccountPrivacy
from app.classroom_models import (
    ClassroomClass, ClassroomClassState, ClassroomEntitlement, ClassroomEntryCode,
    ClassroomMembershipHold, ClassroomMembershipPeriod, ClassroomS2AuditEvent,
    ClassroomStudentMembership, ClassroomTeachingAssignment,
)
from app.models import TrustedVerifier, WoodchuckProfile
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2 import (
    NOW, OFFSET_WINDOWS, actor, assert_corrupt_history_refused, assert_institutional_offset_window,
    code, create, institutional, join, ready, student, trial,
)


@pytest.fixture
def s2_pg(classroom_pg, monkeypatch):
    monkeypatch.setenv("CLASSROOM_S2_ENABLED", "1")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-classroom-s2-only-secret")
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "synthetic-classroom-s2-admin")
    monkeypatch.setattr(service, "_now", lambda: NOW)
    with classroom_pg() as session:
        adults = list(session.scalars(select(TrustedVerifier)))
        for adult in adults:
            adult.email = f"adult{adult.id}@example.test"
        session.add(WoodchuckProfile(id=1, woodchuck_id="WC-SYNTHETIC",
            display_name="Synthetic Student", pin_hash=adults[0].pin_hash,
            instrument="Trumpet", level="Beginner", goal="Build daily consistency"))
        session.flush()
        session.add(AccountPrivacy(profile_id=1, age_band="13to17", declared_at=NOW, public_from=NOW))
        session.commit()
    return classroom_pg


@pytest.mark.parametrize("start_hours,end_hours,offset,allowed", OFFSET_WINDOWS)
def test_institutional_offsets_preserve_approved_window(s2_pg, start_hours, end_hours, offset, allowed):
    assert_institutional_offset_window(s2_pg, start_hours, end_hours, offset, allowed)


@pytest.mark.parametrize("shape", ["closed_closed", "closed_open"])
@pytest.mark.parametrize("operation", ["join", "leave", "suspend", "remove", "reinstate"])
def test_all_membership_writers_refuse_corrupt_history_without_changes(s2_pg, shape, operation):
    assert_corrupt_history_refused(s2_pg, shape, operation)


def setup_class(factory, *, limit=None):
    with factory() as session:
        if limit is None:
            row, _, _ = ready(session)
        else:
            institutional(session, class_limit=limit)
            row = create(session)
            code(session, row.id)
        session.commit()
        return row.id


def results(factory, first, second, expected=("ok", "denied"), **kwargs):
    output = ordered_wait(factory, first, second, **kwargs)
    assert tuple(result[0] for result in output) == expected, output
    return output


def test_simultaneous_trial_start_observes_wait_and_creates_one_trial(s2_pg):
    results(s2_pg, lambda session: trial(session).id, lambda session: trial(session).id)
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomEntitlement)) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action == "trial_started")) == 1


def test_trial_rollback_releases_slot_and_audit_atomically(s2_pg):
    results(s2_pg, lambda session: trial(session).id, lambda session: trial(session).id,
            expected=("ok", "ok"), rollback_first=True)
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomEntitlement)) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action == "trial_started")) == 1


def test_class_creation_at_boundary_serializes_capacity(s2_pg):
    setup_class(s2_pg, limit=2)
    results(s2_pg, lambda session: create(session, name="Second").id,
            lambda session: create(session, name="Must not exist").id)
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomClassState).where(
            ClassroomClassState.state == "active")) == 2
        assert session.scalar(select(func.count()).select_from(ClassroomClass)) == 2


def test_same_program_code_collision_serializes_digest_uniqueness(s2_pg):
    with s2_pg() as session:
        trial(session)
        first, second = create(session, name="One"), create(session, name="Two")
        first_id, second_id = first.id, second.id
        session.commit()
    results(s2_pg, lambda session: code(session, first_id)[0].id,
            lambda session: code(session, second_id)[0].id)
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomEntryCode).where(
            ClassroomEntryCode.is_current.is_(True))) == 1


@pytest.mark.parametrize("rotation_first", [True, False])
def test_rotation_vs_join_final_generation_checked_after_observed_wait(s2_pg, rotation_first):
    class_id = setup_class(s2_pg)
    with s2_pg() as session:
        intent = service.prepare_entry(session, student=student(session), program_id=1, code="BAND2026")
        session.commit()
    rotate = lambda session: code(session, class_id, "ROTATED9")[0].id
    enter = lambda session: join(session, intent=intent).id
    if rotation_first:
        results(s2_pg, rotate, enter)
    else:
        results(s2_pg, enter, rotate, expected=("ok", "ok"))
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == (0 if rotation_first else 1)
        assert session.scalar(select(func.count()).select_from(ClassroomEntryCode).where(
            ClassroomEntryCode.is_current.is_(True))) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomEntryCode)) == 2


def test_duplicate_join_serializes_stable_anchor_and_single_open_period(s2_pg):
    setup_class(s2_pg)
    output = results(s2_pg, lambda session: join(session).id,
                     lambda session: join(session).id, expected=("ok", "ok"))
    assert output[0][1] == output[1][1]
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomMembershipPeriod)) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action == "student_joined")) == 1


@pytest.mark.parametrize("suspend_first", [True, False])
def test_suspend_vs_code_reentry_never_undoes_hold(s2_pg, monkeypatch, suspend_first):
    class_id = setup_class(s2_pg)
    with s2_pg() as session:
        membership_id = join(session).id
        session.commit()
    monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=1))
    suspend = lambda session: service.suspend_member(session, actor=actor(session), program_id=1,
        class_id=class_id, membership_id=membership_id, reason="conduct")
    reenter = lambda session: join(session).id
    if suspend_first:
        results(s2_pg, suspend, reenter)
    else:
        results(s2_pg, reenter, suspend, expected=("ok", "ok"))
    with s2_pg() as session:
        assert session.get(ClassroomMembershipHold, membership_id).released_at is None
        assert not service.contribution_active(session, program_id=1, class_id=class_id, profile_id=1)
        assert session.scalar(select(func.count()).select_from(ClassroomMembershipPeriod).where(
            ClassroomMembershipPeriod.ended_at.is_(None), ClassroomMembershipPeriod.state == "active")) == 0


def test_archive_waiter_can_create_after_capacity_release(s2_pg):
    class_id = setup_class(s2_pg, limit=1)
    results(s2_pg, lambda session: service.set_class_state(session, actor=actor(session),
        program_id=1, class_id=class_id, state="archived"),
        lambda session: create(session, name="Replacement").id, expected=("ok", "ok"))
    with s2_pg() as session:
        assert session.get(ClassroomClassState, class_id).state == "archived"
        assert session.scalar(select(func.count()).select_from(ClassroomClass)) == 2


def test_reactivation_competes_for_last_slot(s2_pg):
    class_id = setup_class(s2_pg, limit=1)
    with s2_pg() as session:
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=class_id, state="archived")
        session.commit()
    results(s2_pg, lambda session: create(session, name="Replacement").id,
        lambda session: service.set_class_state(session, actor=actor(session),
            program_id=1, class_id=class_id, state="active"))
    with s2_pg() as session:
        assert session.get(ClassroomClassState, class_id).state == "archived"
        assert session.scalar(select(func.count()).select_from(ClassroomClassState).where(
            ClassroomClassState.state == "active")) == 1


def test_entitlement_termination_vs_join_rechecks_after_wait(s2_pg):
    setup_class(s2_pg, limit=2)
    results(s2_pg, lambda session: institutional(session, class_limit=2, status="ended").id,
            lambda session: join(session).id)
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == 0
        assert not service.entitlement_decision(session, 1).allowed


def test_exact_expiry_while_program_locked_refuses_waiting_join(s2_pg, monkeypatch):
    setup_class(s2_pg)
    clock = [NOW]
    monkeypatch.setattr(service, "_now", lambda: clock[0])
    with s2_pg() as session:
        intent = service.prepare_entry(session, student=student(session), program_id=1, code="BAND2026")
        session.commit()

    def lock_until_expiry(session):
        classroom._lock_program(session, 1)
        clock[0] = NOW + timedelta(days=14)
        return "clock advanced at Program lock"

    results(s2_pg, lock_until_expiry, lambda session: join(session).id)
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == 0


@pytest.mark.parametrize("operation", ["join", "create", "reactivate", "teacher"])
def test_downgrade_serializes_allowance_sensitive_operations(s2_pg, operation):
    class_id = setup_class(s2_pg, limit=3)
    with s2_pg() as session:
        second = create(session, name="Second")
        archived = create(session, name="Archived")
        archived_id = archived.id
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=archived_id, state="archived")
        session.commit()

    def competing(session):
        if operation == "join":
            return join(session).id
        if operation == "create":
            return create(session, name="Must not exist").id
        if operation == "reactivate":
            return service.set_class_state(session, actor=actor(session), program_id=1,
                class_id=archived_id, state="active")
        return service.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=class_id, verifier_id=2)

    results(s2_pg, lambda session: institutional(session, class_limit=1).id, competing)
    with s2_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomClass)) == 3
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == 0
        assert not service.entitlement_decision(session, 1).allowed


def test_distinct_teaching_adult_boundary_serializes(s2_pg):
    class_id = setup_class(s2_pg)
    with s2_pg() as session:
        service.assign_teacher(session, actor=actor(session), program_id=1, class_id=class_id, verifier_id=1)
        session.commit()
    results(s2_pg,
        lambda session: service.assign_teacher(session, actor=actor(session), program_id=1, class_id=class_id, verifier_id=2),
        lambda session: service.assign_teacher(session, actor=actor(session), program_id=1, class_id=class_id, verifier_id=3))
    with s2_pg() as session:
        assert session.scalar(select(func.count(func.distinct(ClassroomTeachingAssignment.verifier_id))).where(
            ClassroomTeachingAssignment.ended_at.is_(None))) == 2


def test_reactivation_vs_teacher_activation_rechecks_distinct_adult_union(s2_pg):
    with s2_pg() as session:
        institutional(session, teacher_limit=1)
        first, second = create(session, name="One"), create(session, name="Two")
        first_id, second_id = first.id, second.id
        service.assign_teacher(session, actor=actor(session), program_id=1, class_id=first_id, verifier_id=2)
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=first_id, state="archived")
        session.commit()
    results(s2_pg,
        lambda session: service.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=second_id, verifier_id=3),
        lambda session: service.set_class_state(session, actor=actor(session), program_id=1,
            class_id=first_id, state="active"))
    with s2_pg() as session:
        assert session.get(ClassroomClassState, first_id).state == "archived"
        assert service.entitlement_decision(session, 1).allowed
