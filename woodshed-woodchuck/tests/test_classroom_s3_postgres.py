"""S3 authorization races on an explicitly disposable loopback PostgreSQL DB.

Every ordered race observes pg_blocking_pids before releasing its first
transaction. Authorization proofs carry no approved student data fields.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import classroom, classroom_reporting as reporting, classroom_s2 as s2
from app.classroom_models import (
    ClassroomClassState, ClassroomEntitlement, ClassroomMembershipPeriod,
    ClassroomReportingPeriod, ClassroomS3AuditEvent, ClassroomTeachingAssignment,
)
from app.models import TrustedVerifier, WoodchuckProfile
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2 import NOW, actor, code, create, institutional, join, student
from test_classroom_s2_postgres import s2_pg


@pytest.fixture
def s3_pg(s2_pg, monkeypatch):
    monkeypatch.setenv("CLASSROOM_S3_ENABLED", "1")
    clock = [NOW]
    monkeypatch.setattr(reporting, "_now", lambda: clock[0])
    monkeypatch.setattr(s2, "_now", lambda: clock[0])
    s2_pg.reporting_clock = clock
    return s2_pg


def setup_reporting(factory, *, granted=True):
    with factory() as session:
        entitlement = institutional(session)
        row = create(session)
        code(session, row.id)
        member = join(session)
        teacher = s2.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=row.id, verifier_id=2)
        period = grant(session, row.id) if granted else None
        refs = SimpleNamespace(class_id=row.id, membership_id=member.id,
            assignment_id=teacher.id, entitlement_id=entitlement.id,
            period_id=period.id if period else None)
        session.commit()
    return refs


def grant(session, class_id):
    return reporting.grant_reporting(session, student=student(session),
        program_id=1, class_id=class_id,
        scope_version="classroom-boundary-v1",
        notice_version="classroom-boundary-notice-v1")


def withdraw(session, class_id):
    reporting.withdraw_reporting(session, student=student(session),
        program_id=1, class_id=class_id)


def reauthorize(session, class_id):
    return reporting.reauthorize_reporting(session, student=student(session),
        program_id=1, class_id=class_id,
        scope_version="classroom-boundary-v1",
        notice_version="classroom-boundary-notice-v1")


def read(session, refs, *, preload=False):
    authenticated = actor(session, 2)
    if preload:
        # The first transaction's uncommitted change is invisible. Retain these
        # objects so stale identity-map values cannot accidentally disappear.
        cached = (
            session.get(ClassroomReportingPeriod, refs.period_id),
            session.get(ClassroomTeachingAssignment, refs.assignment_id),
            session.get(ClassroomClassState, refs.class_id),
            session.get(ClassroomEntitlement, refs.entitlement_id),
            session.scalar(select(ClassroomMembershipPeriod).where(
                ClassroomMembershipPeriod.membership_id == refs.membership_id,
                ClassroomMembershipPeriod.ended_at.is_(None))),
        )
        assert cached[0].ended_at is None
        assert cached[1].ended_at is None
        assert cached[2].state == "active"
        assert cached[3].status == "active"
        assert cached[4] is not None
    decision = reporting.authorize_reporting(session, actor=authenticated,
        program_id=1, class_id=refs.class_id, profile_id=1,
        operation="history_boundary", fields=())
    assert decision.allowed
    return "authorized internal boundary; no data fields"


def race(factory, first, second, expected=("ok", "denied"), **kwargs):
    outcomes = ordered_wait(factory, first, second, **kwargs)
    assert tuple(item[0] for item in outcomes) == expected, outcomes
    return outcomes


def rows(session):
    return list(session.scalars(select(ClassroomReportingPeriod).order_by(
        ClassroomReportingPeriod.id)))


def audit_actions(session):
    return list(session.scalars(select(ClassroomS3AuditEvent.action).order_by(
        ClassroomS3AuditEvent.id)))


@pytest.mark.parametrize("withdraw_first", [False, True])
def test_grant_vs_withdraw_is_ordered_by_observed_wait(s3_pg, withdraw_first):
    refs = setup_reporting(s3_pg, granted=False)
    create_grant = lambda session: grant(session, refs.class_id).id
    revoke = lambda session: withdraw(session, refs.class_id)
    first, second = (revoke, create_grant) if withdraw_first else (create_grant, revoke)
    race(s3_pg, first, second, expected=("ok", "ok"))
    with s3_pg() as session:
        periods = rows(session)
        assert len(periods) == 1
        assert (periods[0].ended_at is None) is withdraw_first
        assert audit_actions(session) == (["reporting_granted"] if withdraw_first else
            ["reporting_granted", "reporting_withdrawn"])


@pytest.mark.parametrize("rollback_first", [False, True])
def test_duplicate_grant_wait_preserves_one_period_and_atomic_audit(s3_pg, rollback_first):
    refs = setup_reporting(s3_pg, granted=False)
    race(s3_pg, lambda session: grant(session, refs.class_id).id,
        lambda session: grant(session, refs.class_id).id,
        expected=("ok", "ok" if rollback_first else "denied"),
        rollback_first=rollback_first)
    with s3_pg() as session:
        assert len(rows(session)) == 1
        assert rows(session)[0].ended_at is None
        assert audit_actions(session) == ["reporting_granted"]


@pytest.mark.parametrize("withdraw_first", [False, True])
def test_reporting_read_vs_withdraw_fresh_after_wait(s3_pg, withdraw_first):
    refs = setup_reporting(s3_pg)
    s3_pg.reporting_clock[0] += timedelta(minutes=1)
    revoke = lambda session: withdraw(session, refs.class_id)
    inspect = lambda session: read(session, refs, preload=withdraw_first)
    first, second = (revoke, inspect) if withdraw_first else (inspect, revoke)
    race(s3_pg, first, second, expected=("ok", "denied" if withdraw_first else "ok"))
    with s3_pg() as session:
        assert rows(session)[0].ended_at is not None
        assert audit_actions(session) == ["reporting_granted", "reporting_withdrawn"]
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)


@pytest.mark.parametrize("operation", ["teacher_departure", "suspend", "leave", "entitlement_end", "archive"])
@pytest.mark.parametrize("writer_first", [False, True])
def test_lifecycle_writer_vs_read_observes_lock_and_current_authority(s3_pg, operation, writer_first):
    refs = setup_reporting(s3_pg)
    s3_pg.reporting_clock[0] += timedelta(minutes=1)

    def mutate(session):
        if operation == "teacher_departure":
            return s2.end_teaching(session, actor=actor(session), program_id=1,
                assignment_id=refs.assignment_id).id
        if operation == "suspend":
            return s2.suspend_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id, reason="conduct").id
        if operation == "leave":
            return s2.leave_class(session, student=student(session), program_id=1,
                class_id=refs.class_id).id
        if operation == "entitlement_end":
            return institutional(session, status="ended").id
        return s2.set_class_state(session, actor=actor(session), program_id=1,
            class_id=refs.class_id, state="archived").class_id

    inspect = lambda session: read(session, refs, preload=writer_first)
    first, second = (mutate, inspect) if writer_first else (inspect, mutate)
    race(s3_pg, first, second, expected=("ok", "denied" if writer_first else "ok"))
    with s3_pg() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        # Ending one source does not delete the reporting decision or history.
        assert len(rows(session)) == 1


def test_entitlement_replacement_cannot_rescue_preloaded_old_grant_after_wait(s3_pg):
    refs = setup_reporting(s3_pg)
    s3_pg.reporting_clock[0] += timedelta(minutes=1)
    race(s3_pg, lambda session: institutional(session).id,
        lambda session: read(session, refs, preload=True))
    with s3_pg() as session:
        assert s2.entitlement_decision(session, 1).allowed
        assert session.scalar(select(func.count()).select_from(ClassroomEntitlement)) == 2
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)


def test_exact_entitlement_expiry_while_program_locked_denies_waiting_read(s3_pg, monkeypatch):
    refs = setup_reporting(s3_pg)
    control = s3_pg.control_engine

    class ExpiryObserver:
        def __enter__(self):
            self.connection = control.connect()
            return self

        def __exit__(self, *exc):
            self.connection.close()

        def scalar(self, statement, parameters):
            blockers = self.connection.scalar(statement, parameters)
            # Advance only AFTER PostgreSQL proves a live wait. A timestamp
            # captured before the Program lock must not authorize this read.
            if blockers:
                s3_pg.reporting_clock[0] = NOW + timedelta(days=365)
            return blockers

    monkeypatch.setattr(s3_pg, "control_engine", SimpleNamespace(connect=ExpiryObserver))

    def lock_until_expiry(session):
        classroom._lock_program(session, 1)
        return "Program lock held across the synthetic entitlement expiry"

    race(s3_pg, lock_until_expiry, lambda session: read(session, refs, preload=True))
    assert s3_pg.reporting_clock[0] == NOW + timedelta(days=365)


@pytest.mark.parametrize("reauthorize_first", [False, True])
def test_reauthorization_and_withdrawal_keep_distinct_periods_after_wait(s3_pg, reauthorize_first):
    refs = setup_reporting(s3_pg)
    s3_pg.reporting_clock[0] += timedelta(minutes=1)
    if reauthorize_first:
        with s3_pg() as session:
            withdraw(session, refs.class_id)
            session.commit()
        s3_pg.reporting_clock[0] += timedelta(minutes=1)
    revoke = lambda session: withdraw(session, refs.class_id)
    renew = lambda session: reauthorize(session, refs.class_id).id
    first, second = (renew, revoke) if reauthorize_first else (revoke, renew)
    race(s3_pg, first, second, expected=("ok", "ok"))
    with s3_pg() as session:
        periods = rows(session)
        assert len(periods) == 2
        assert periods[0].ended_at is not None
        assert periods[1].starts_at >= periods[0].ended_at
        assert (periods[1].ended_at is None) is not reauthorize_first
        assert audit_actions(session) == (["reporting_granted", "reporting_withdrawn",
            "reporting_reauthorized", "reporting_withdrawn"] if reauthorize_first else
            ["reporting_granted", "reporting_withdrawn", "reporting_reauthorized"])


def test_withdrawal_rollback_preserves_period_and_read_authority(s3_pg):
    refs = setup_reporting(s3_pg)
    s3_pg.reporting_clock[0] += timedelta(minutes=1)
    race(s3_pg, lambda session: withdraw(session, refs.class_id),
        lambda session: read(session, refs, preload=True),
        expected=("ok", "ok"), rollback_first=True)
    with s3_pg() as session:
        assert rows(session)[0].ended_at is None
        assert audit_actions(session) == ["reporting_granted"]


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_reporting_reads_refuse_snapshot_isolation(s3_pg, isolation):
    refs = setup_reporting(s3_pg)
    with s3_pg.kw["bind"].connect().execution_options(isolation_level=isolation) as connection:
        with s3_pg(bind=connection) as session:
            with pytest.raises(classroom.ClassroomDenied):
                read(session, refs)


@pytest.mark.parametrize("writer_first", [False, True])
def test_underlying_account_inactivation_vs_read_observes_profile_lock(s3_pg, writer_first):
    refs = setup_reporting(s3_pg)

    def deactivate(session):
        profile = classroom._fresh(session, WoodchuckProfile, WoodchuckProfile.id == 1)
        profile.status = "deleted"
        session.flush()
        return profile.id

    inspect = lambda session: read(session, refs)
    first, second = (deactivate, inspect) if writer_first else (inspect, deactivate)
    race(s3_pg, first, second, expected=("ok", "denied" if writer_first else "ok"))
    with s3_pg() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)


@pytest.mark.parametrize("credential", ["adult", "student"])
def test_credentials_changed_during_program_wait_refuse_stale_proof(s3_pg, credential):
    refs = setup_reporting(s3_pg, granted=credential == "adult")

    def change_credentials(session):
        classroom._lock_program(session, 1)
        if credential == "adult":
            from app.security import hash_pin
            session.get(TrustedVerifier, 2).pin_hash = hash_pin("9876")
        else:
            session.get(WoodchuckProfile, 1).session_version += 1
        session.flush()

    # Authentication sees the previously committed credentials. The service
    # must recheck that proof after the observed Program-lock wait finishes.
    inspect = (lambda session: read(session, refs, preload=True)) if credential == "adult" else (
        lambda session: grant(session, refs.class_id).id)
    race(s3_pg, change_credentials, inspect)
    with s3_pg() as session:
        assert len(rows(session)) == (1 if credential == "adult" else 0)
        assert audit_actions(session) == (["reporting_granted"] if credential == "adult" else [])
