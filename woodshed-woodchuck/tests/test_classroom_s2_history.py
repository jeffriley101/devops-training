"""Persisted S2 history must win over a retained ORM cache on both backends."""
from datetime import timedelta

from alembic import command
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import classroom, classroom_s2 as service, models as m
from app.age_privacy import utc
from app.classroom_models import ClassroomMembershipHold, ClassroomS2AuditEvent
from app.db import Base
from test_classroom_s1 import adult_pin_hash
from test_classroom_s2_migration import NOW, config, migrated_c22
from tests.test_persistent_team_migration import disposable_sqlite_configuration


def adult(session):
    return classroom.authenticate_adult(session, email="adult@example.invalid", pin="2468")


def student(session):
    return service.authenticate_student(session, woodchuck_id="WC-S2-MIGRATION", pin="2468")


@pytest.fixture
def migrated_history(migrated_c22, monkeypatch, adult_pin_hash):
    command.upgrade(config(), "c23class001")
    monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
    monkeypatch.setenv("CLASSROOM_S2_ENABLED", "1")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-classroom-s2-history-secret")
    clock = [NOW]
    monkeypatch.setattr(service, "_now", lambda: clock[0])
    # Match the application session, including its retained identities and
    # disabled autoflush: reinstatement closes then opens in one transaction.
    factory = sessionmaker(migrated_c22, expire_on_commit=False, autoflush=False)
    with factory() as session:
        session.get(m.TrustedVerifier, 1).pin_hash = adult_pin_hash
        session.get(m.WoodchuckProfile, 1).pin_hash = adult_pin_hash
        session.add(m.AccountPrivacy(profile_id=1, age_band="13to17", declared_at=NOW, public_from=NOW))
        session.commit()
        service.start_trial(session, actor=adult(session), program_id=1)
        service.set_class_state(session, actor=adult(session), program_id=1, class_id=1, state="active")
        service.set_enrollment(session, actor=adult(session), program_id=1, class_id=1, open=True)
        service.issue_code(session, actor=adult(session), program_id=1, class_id=1, preferred="BAND2026")
        session.commit()
    return factory, clock


def history(session):
    return list(session.scalars(select(m.ClassroomMembershipPeriod).where(
        m.ClassroomMembershipPeriod.membership_id == 1).order_by(m.ClassroomMembershipPeriod.starts_at)))


def snapshot(session):
    # Core rows bypass the identity map; include every Classroom column so
    # changes to holds, anchors, periods, authority or success audits are visible.
    return {table.name: list(session.execute(select(table).order_by(*table.primary_key.columns)))
            for table in Base.metadata.sorted_tables if table.name.startswith("classroom_")}


def corrupt_persisted_history(engine, period_id):
    """Temporarily bypass only the overlap UPDATE guard in a disposable fixture.

    Restore it before any service call: an unrelated successful period write
    does not prove that the rest of the persisted history is uncorrupted.
    """
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            guard = connection.scalar(text("SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                                           "AND name = 'classroom_period_no_overlap_update'"))
            assert guard
            connection.execute(text("DROP TRIGGER classroom_period_no_overlap_update"))
        else:
            connection.execute(text("ALTER TABLE classroom_membership_periods "
                                    "DISABLE TRIGGER classroom_period_no_overlap"))
        connection.execute(m.ClassroomMembershipPeriod.__table__.update().where(
            m.ClassroomMembershipPeriod.id == period_id).values(ended_at=NOW - timedelta(minutes=15)))
        if engine.dialect.name == "sqlite":
            connection.execute(text(guard))
        else:
            connection.execute(text("ALTER TABLE classroom_membership_periods "
                                    "ENABLE TRIGGER classroom_period_no_overlap"))


def membership_operation(session, operation, *, authentication=None):
    if operation == "join":
        return service.join_class(session, student=authentication or student(session),
                                  program_id=1, code="BAND2026")
    if operation == "leave":
        return service.leave_class(session, student=authentication or student(session),
                                   program_id=1, class_id=1)
    if operation == "reinstate":
        return service.reinstate_member(session, actor=authentication or adult(session), program_id=1,
                                        class_id=1, membership_id=1)
    if operation in {"suspend", "remove"}:
        return service.suspend_member(session, actor=authentication or adult(session), program_id=1,
            class_id=1, membership_id=1, remove=operation == "remove",
            reason="admin_removal" if operation == "remove" else "conduct")
    # Shared helpers have the same Program -> Class -> anchor lock precondition
    # as the public writers and run inside the caller's savepoint.
    with session.begin_nested():
        classroom._lock_program(session, 1)
        classroom._class(session, 1, 1)
        anchor = service._anchor(session, 1, 1)
        if operation == "validate":
            return service._validated_periods(session, anchor.id)
        if operation == "open":
            return service._open_period(session, anchor, service._now())
        assert operation == "close"
        return service._close_period(session, anchor, service._now(), "departed")


@pytest.mark.parametrize("shape", ["closed_closed", "closed_open"])
@pytest.mark.parametrize("operation", ["join", "leave", "suspend", "remove", "reinstate",
                                       "validate", "open", "close"])
def test_stale_history_refused_without_partial_mutation(migrated_history, shape, operation):
    factory, _ = migrated_history
    with factory() as session:
        for start, end in ((40, 30), (20, None if shape == "closed_open" else 10)):
            session.add(m.ClassroomMembershipPeriod(membership_id=1,
                starts_at=NOW - timedelta(minutes=start),
                ended_at=NOW - timedelta(minutes=end) if end is not None else None,
                state="active", ended_reason="departed" if end is not None else None))
        if operation == "reinstate":
            session.add(ClassroomMembershipHold(membership_id=1, state="suspended", reason="conduct",
                held_by_verifier_id=1, held_at=NOW - timedelta(minutes=5)))
        session.commit()
        # Student sign-in expires the session cache, so authenticate BEFORE
        # loading periods and retain that proof through the writer call.
        authentication = student(session) if operation in {"join", "leave"} else adult(session)
        # Keep strong references: an unreferenced ORM row can disappear from the
        # identity map, turning this into a fresh-session test of different behavior.
        cached = history(session)
        corrupt_persisted_history(session.get_bind(), cached[0].id)
        assert utc(cached[0].ended_at) == NOW - timedelta(minutes=30)
        assert utc(session.scalar(select(m.ClassroomMembershipPeriod.ended_at).where(
            m.ClassroomMembershipPeriod.id == cached[0].id))) == NOW - timedelta(minutes=15)
        assert not session.dirty
        before = snapshot(session)
        refusal = None
        try:
            membership_operation(session, operation, authentication=authentication)
        except classroom.ClassroomDenied as error:
            refusal = error
        # Commit the caller's transaction even after refusal, so an outer
        # rollback cannot hide a partial mutation or a success audit.
        session.commit()
    with factory() as session:
        after = snapshot(session)
    assert refusal is not None, f"{operation} accepted stale history; committed changes: {after != before}"
    assert "Membership history overlaps" in str(refusal)
    assert after == before


@pytest.mark.parametrize("remove", [False, True])
def test_migrated_lifecycle_with_retained_periods(migrated_history, remove):
    factory, clock = migrated_history
    with factory() as session:
        assert membership_operation(session, "join").id == 1
        assert membership_operation(session, "join").id == 1
        cached = history(session)
        assert len(cached) == 1
        session.commit()
        clock[0] = NOW + timedelta(minutes=1)
        membership_operation(session, "leave")
        session.commit()
        assert utc(cached[0].ended_at) == clock[0]
        clock[0] = NOW + timedelta(minutes=2)
        assert membership_operation(session, "join").id == 1
        cached = history(session)
        session.commit()
        clock[0] = NOW + timedelta(minutes=3)
        membership_operation(session, "remove" if remove else "suspend")
        session.commit()
        assert utc(cached[-1].ended_at) == clock[0]
        hold = session.get(ClassroomMembershipHold, 1)
        assert hold.state == ("removed" if remove else "suspended")
        assert hold.released_at is None
        before = snapshot(session)
        with pytest.raises(classroom.ClassroomDenied, match="explicit reinstatement"):
            membership_operation(session, "join")
        session.commit()
        assert snapshot(session) == before
        clock[0] = NOW + timedelta(minutes=4)
        membership_operation(session, "reinstate")
        session.commit()
    with factory() as session:
        periods = history(session)
        assert [utc(p.starts_at) for p in periods] == [NOW, NOW + timedelta(minutes=2), clock[0]]
        assert [utc(p.ended_at) if p.ended_at else None for p in periods] == [
            NOW + timedelta(minutes=1), NOW + timedelta(minutes=3), None]
        assert all(p.state == "active" for p in periods)
        assert utc(session.get(ClassroomMembershipHold, 1).released_at) == clock[0]
        assert service.contribution_active(session, program_id=1, class_id=1, profile_id=1)
        actions = list(session.scalars(select(ClassroomS2AuditEvent.action).where(
            ClassroomS2AuditEvent.membership_id == 1).order_by(ClassroomS2AuditEvent.id)))
        assert actions == ["student_joined", "student_left", "student_joined",
                           "student_removed" if remove else "student_suspended", "student_reinstated"]
