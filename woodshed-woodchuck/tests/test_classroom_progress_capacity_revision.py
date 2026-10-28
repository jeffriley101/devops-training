"""Program usage history must outlive recovery, including changes in other Classes."""
from datetime import timedelta
import hashlib
import json

import pytest
from sqlalchemy import event, func, select

from app import classroom as s1, classroom_s2 as s2, classroom_reporting as boundary
from app import classroom_progress_authorization as progress
from app.age_privacy import utc
from app.classroom_models import (
    ClassroomAuditEvent, ClassroomS2AuditEvent, ClassroomProgram,
    ClassroomTeachingAssignment, ClassroomProgressAuthorization as Authorization,
)
from app.models import Organization
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, actor, student, institutional, create
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2_postgres import s2_pg
from test_classroom_s3_postgres import s3_pg, setup_reporting
from test_classroom_progress_authorization import progress_db, read, withdraw
from test_classroom_progress_source_revision import reviewed_options
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web, student_client
from test_classroom_progress_routes import progress_web, form, url


def submit(session, options):
    return progress.accept(session, student=student(session), **options)


def prepare(factory, *, teachers=1, classes=4):
    with factory() as session:
        package = institutional(session, teacher_limit=teachers, class_limit=classes)
        other = create(session, name="Other capacity Class")
        ids = package.id, other.id
        session.commit()
    return ids


def add_other(factory, monkeypatch, class_id, *, verifier=3):
    # S1 remains a supported writer during S2 downtime; every source mutation
    # still takes the Program lock and appends its own real audit record.
    with monkeypatch.context() as gate:
        gate.delenv("CLASSROOM_S2_ENABLED")
        with factory() as session:
            row = s1.assign_teacher(session, actor=actor(session), program_id=1,
                class_id=class_id, verifier_id=verifier)
            ident = row.id
            session.commit()
    return ident


def end_other(factory, ident, *, departure=False):
    with factory() as session:
        row = session.get(ClassroomTeachingAssignment, ident)
        s2.end_teaching(session, actor=actor(session, row.verifier_id if departure else 1),
            program_id=1, assignment_id=ident, reason="departed" if departure else "revoked")
        session.commit()


def deny_old(session, refs, forms):
    with pytest.raises(s1.ClassroomDenied):
        read(session, refs)
    for reviewed in forms:
        with pytest.raises(s1.ClassroomDenied):
            submit(session, reviewed)


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("observed", [False, True])
@pytest.mark.parametrize("recovery", ["revoked", "departed", "archived"])
def test_cross_class_capacity_interruption_never_restores(progress_db, monkeypatch, saved, observed, recovery):
    factory, refs = progress_db, progress_db.refs
    package, other = prepare(factory)
    with factory() as session:
        old = reviewed_options(session, refs)
        row = submit(session, old) if saved else None
        evidence = (row.id, row.source_revision, utc(row.accepted_at)) if saved else None
        session.commit()
    extra = add_other(factory, monkeypatch, other)
    if observed:
        with factory() as session:
            decision = s2.entitlement_decision(session, 1)
            assert decision.entitlement_id == package and decision.reason == "over_limit"
            deny_old(session, refs, [old])
    if recovery == "archived":
        with factory() as session:
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=other, state="archived")
            session.commit()
    else:
        end_other(factory, extra, departure=recovery == "departed")
    with factory() as session:
        decision = s2.entitlement_decision(session, 1)
        assert decision.allowed and decision.entitlement_id == package
        deny_old(session, refs, [old])
        assert session.scalar(select(func.count()).select_from(Authorization)) == int(saved)
        if saved:
            retained = session.get(Authorization, evidence[0])
            assert (retained.id, retained.source_revision, utc(retained.accepted_at)) == evidence
            assert retained.withdrawn_at is None
        fresh = submit(session, reviewed_options(session, refs))
        assert fresh.source_revision != old["source_revision"] and read(session, refs).allowed
        if saved:
            assert fresh.id != evidence[0] and retained.withdrawal_reason == "replaced"
        session.commit()


@pytest.mark.parametrize("limit", [2, 3])
def test_below_and_exact_capacity_do_not_invalidate_unrelated_consent(progress_db, limit):
    factory, refs = progress_db, progress_db.refs
    _, other = prepare(factory, teachers=limit)
    with factory() as session:
        old = reviewed_options(session, refs)
        ident = submit(session, old).id
        session.commit()
    with factory() as session:
        added = s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=other, verifier_id=3)
        extra = added.id
        assert s2.usage(session, 1) == (2, 2)
        assert s2.entitlement_decision(session, 1).allowed
        session.commit()
    with factory() as session:
        assert reviewed_options(session, refs)["source_revision"] == old["source_revision"]
        assert submit(session, old).id == ident and read(session, refs).allowed
    end_other(factory, extra)
    with factory() as session:
        assert submit(session, old).id == ident and read(session, refs).allowed


def test_distinct_teacher_turnover_conservatively_retains_shared_history(progress_db):
    factory, refs = progress_db, progress_db.refs
    _, other = prepare(factory, teachers=2)
    with factory() as session:
        old = reviewed_options(session, refs)
        submit(session, old)
        extra = s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=other, verifier_id=3).id
        session.commit()
    end_other(factory, extra)
    with factory() as session:
        assert read(session, refs).allowed  # Only two distinct teachers ever existed.
        extra = s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=other, verifier_id=4).id
        assert s2.usage(session, 1) == (2, 2)  # Concurrent usage still fits.
        session.commit()
    end_other(factory, extra)
    with factory() as session:
        # Retained history now contains three distinct teachers. Conservatively
        # invalidate; counting only currently open rows would resurrect old.
        assert s2.usage(session, 1) == (2, 1)
        deny_old(session, refs, [old])
        assert submit(session, reviewed_options(session, refs)) and read(session, refs).allowed


def test_multiple_classes_repeated_interruptions_and_exact_usage_restoration(progress_db, monkeypatch):
    factory, refs = progress_db, progress_db.refs
    package, other = prepare(factory)
    with factory() as session:
        third = create(session, name="Third capacity Class").id
        forms = [reviewed_options(session, refs)]
        submit(session, forms[0])
        session.commit()
    for _ in range(3):
        first = add_other(factory, monkeypatch, other, verifier=3)
        second = add_other(factory, monkeypatch, third, verifier=4)
        end_other(factory, first)
        with factory() as session:
            assert s2.entitlement_decision(session, 1).reason == "over_limit"
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=third, state="archived")
            session.commit()
        end_other(factory, second, departure=True)
        with factory() as session:
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=third, state="active")
            session.commit()
        with factory() as session:
            assert s2.usage(session, 1) == (3, 1)
            assert s2.entitlement_decision(session, 1).entitlement_id == package
            deny_old(session, refs, forms)
            fresh = reviewed_options(session, refs)
            assert fresh["source_revision"] not in {f["source_revision"] for f in forms}
            forms.append(fresh)
            submit(session, fresh)
            assert read(session, refs).allowed
            session.commit()


def test_shared_class_activity_keeps_history_after_other_assignment_ends(progress_db, monkeypatch):
    factory, refs = progress_db, progress_db.refs
    _, other = prepare(factory)
    extra = add_other(factory, monkeypatch, other)
    end_other(factory, extra)
    with factory() as session:
        old = reviewed_options(session, refs)
        submit(session, old)
        session.commit()
    for state in ("archived", "active"):
        with factory() as session:
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=other, state=state)
            session.commit()
        with factory() as session:
            assert s2.entitlement_decision(session, 1).allowed
            deny_old(session, refs, [old])
    with factory() as session:
        assert submit(session, reviewed_options(session, refs)) and read(session, refs).allowed


def test_retained_archived_classes_bound_class_capacity_history(progress_db):
    factory, refs = progress_db, progress_db.refs
    _, other = prepare(factory, teachers=2, classes=2)
    with factory() as session:
        old = reviewed_options(session, refs)
        submit(session, old)
        s2.set_class_state(session, actor=actor(session), program_id=1, class_id=other, state="archived")
        assert read(session, refs).allowed  # Two total Classes cannot exceed two.
        third = create(session, name="Replacement Class").id
        assert s2.usage(session, 1) == (2, 1)
        session.commit()
    with factory() as session:
        deny_old(session, refs, [old])
        fresh = reviewed_options(session, refs)
        submit(session, fresh)
        session.commit()
    with factory() as session:
        s2.set_class_state(session, actor=actor(session), program_id=1, class_id=third, state="archived")
        s2.set_class_state(session, actor=actor(session), program_id=1, class_id=other, state="active")
        session.commit()
    with factory() as session:
        assert s2.usage(session, 1) == (2, 1)
        deny_old(session, refs, [old, fresh])
        assert submit(session, reviewed_options(session, refs)) and read(session, refs).allowed


@pytest.mark.parametrize("capacity_first", [False, True])
def test_capacity_and_scheduled_cycles_preserve_all_interruption_history(progress_db, monkeypatch, capacity_first):
    factory, refs = progress_db, progress_db.refs
    monkeypatch.setenv("CLASSROOM_TEACHING_DIRECTOR_LIMIT", "1")
    now = factory.clock[0]
    with factory() as session:
        institutional(session, status="ended")
        trial = s2.start_trial(session, actor=actor(session), program_id=1)
        trial_id = trial.id
        other = create(session, name="Shared capacity").id
        institutional(session, teacher_limit=1, starts_at=now + timedelta(minutes=5),
            ends_at=now + timedelta(minutes=10))
        forms = [reviewed_options(session, refs)]
        submit(session, forms[0])
        session.commit()
    order = ("capacity", "schedule") if capacity_first else ("schedule", "capacity")
    for change in order:
        if change == "capacity":
            extra = add_other(factory, monkeypatch, other)
            end_other(factory, extra)
        else:
            factory.clock[0] = now + timedelta(minutes=11)  # No request during package interval.
        with factory() as session:
            assert s2.entitlement_decision(session, 1).entitlement_id == trial_id
            deny_old(session, refs, forms)
            fresh = reviewed_options(session, refs)
            forms.append(fresh)
            submit(session, fresh)
            assert read(session, refs).allowed
            session.commit()


def test_capacity_audit_failure_leaves_no_partial_revision(progress_db, monkeypatch):
    factory, refs = progress_db, progress_db.refs
    _, other = prepare(factory)
    with factory() as session:
        old = reviewed_options(session, refs)
        ident = submit(session, old).id
        session.commit()
    def reject(mapper, connection, target):
        raise RuntimeError("independent shared-capacity audit failure")
    event.listen(ClassroomAuditEvent, "before_insert", reject)
    try:
        with monkeypatch.context() as gate:
            gate.delenv("CLASSROOM_S2_ENABLED")
            with factory() as session:
                with pytest.raises(RuntimeError, match="shared-capacity audit failure"):
                    s1.assign_teacher(session, actor=actor(session), program_id=1, class_id=other, verifier_id=3)
                session.commit()
    finally:
        event.remove(ClassroomAuditEvent, "before_insert", reject)
    with factory() as session:
        assert s2.usage(session, 1) == (2, 1)
        assert submit(session, old).id == ident and read(session, refs).allowed


def test_source_v2_evidence_and_forms_require_fresh_v3_acceptance(progress_db):
    factory, refs = progress_db, progress_db.refs
    with factory() as session:
        old = reviewed_options(session, refs)
        row = submit(session, old)
        anchor, member, entitlement, rule, activation, _ = boundary._context(
            session, 1, refs.class_id, 1, student=student(session))
        # Frozen pre-P1-D v2 contract for the single-package fixture. Deliberately
        # excludes the new shared history; never silently backfill old evidence.
        snapshot = ("classroom-progress-source-v2", 1, refs.class_id, 1,
            session.scalar(select(func.max(ClassroomAuditEvent.id)).where(ClassroomAuditEvent.program_id == 1)),
            session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(ClassroomS2AuditEvent.program_id == 1)),
            anchor.id, member.id, entitlement.id, utc(rule.declared_at).isoformat(), activation.isoformat(),
            progress.NOTICE_DIGEST, utc(entitlement.starts_at).isoformat())
        legacy = hashlib.sha256(json.dumps(snapshot, separators=(",", ":")).encode()).hexdigest()
        row.source_revision = legacy
        ident, timestamp = row.id, utc(row.accepted_at)
        session.commit()
    with factory() as session:
        deny_old(session, refs, [old | {"source_revision": legacy}])
        retained = session.get(Authorization, ident)
        assert retained.source_revision == legacy and utc(retained.accepted_at) == timestamp
        assert retained.withdrawn_at is None
        fresh = submit(session, reviewed_options(session, refs))
        assert fresh.id != ident and fresh.source_revision != legacy and read(session, refs).allowed
        assert retained.withdrawal_reason == "replaced" and retained.source_revision == legacy


def test_http_reauthorization_forms_stay_stale_across_repeated_capacity_cycles(progress_web, monkeypatch):
    factory = progress_web
    _, other = prepare(factory)
    client = student_client()
    original = form(client)
    assert client.post(url(factory), data=original, follow_redirects=False).status_code == 303
    assert client.post(url(factory, "withdraw"), data={"csrf": original["csrf"]},
        follow_redirects=False).status_code == 303
    forms = [form(client)]
    for _ in range(3):
        extra = add_other(factory, monkeypatch, other)
        end_other(factory, extra)
        for old in forms:
            assert client.post(url(factory), data=old, follow_redirects=False).status_code == 409
        forms.append(form(client))
    with factory() as session:
        rows = list(session.scalars(select(Authorization)))
        assert len(rows) == 1 and rows[0].withdrawn_at is not None
    assert client.post(url(factory), data=forms[-1], follow_redirects=False).status_code == 303
    with factory() as session:
        rows = list(session.scalars(select(Authorization).order_by(Authorization.id)))
        assert len(rows) == 2 and rows[0].withdrawn_at is not None and rows[1].withdrawn_at is None


def capacity_cycle(session, monkeypatch, other):
    with monkeypatch.context() as gate:
        gate.delenv("CLASSROOM_S2_ENABLED")
        extra = s1.assign_teacher(session, actor=actor(session), program_id=1, class_id=other, verifier_id=3)
        s1.end_teaching(session, actor=actor(session), program_id=1, assignment_id=extra.id)
    return "retained capacity interruption"


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("mutation_first", [False, True])
def test_postgres_capacity_history_serializes_with_acceptance(s3_pg, monkeypatch, saved, mutation_first):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    _, other = prepare(s3_pg)
    with s3_pg() as session:
        assert session.connection().get_isolation_level() == "READ COMMITTED"
        old = reviewed_options(session, refs)
        if saved:
            submit(session, old)
        session.commit()
    mutate = lambda session: capacity_cycle(session, monkeypatch, other)
    accept = lambda session: submit(session, old).id
    functions = (mutate, accept) if mutation_first else (accept, mutate)
    outcomes = ordered_wait(s3_pg, *functions)
    assert tuple(r[0] for r in outcomes) == (("ok", "denied") if mutation_first else ("ok", "ok"))
    with s3_pg() as session:
        deny_old(session, refs, [old])
        assert session.scalar(select(func.count()).select_from(Authorization)) == (int(saved) if mutation_first else 1)
        assert submit(session, reviewed_options(session, refs)) and read(session, refs).allowed


def test_postgres_capacity_rollback_preserves_reviewed_initial_acceptance(s3_pg, monkeypatch):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    _, other = prepare(s3_pg)
    with s3_pg() as session:
        old = reviewed_options(session, refs)
    outcomes = ordered_wait(s3_pg, lambda session: capacity_cycle(session, monkeypatch, other),
        lambda session: submit(session, old).id, rollback_first=True)
    assert tuple(r[0] for r in outcomes) == ("ok", "ok")
    with s3_pg() as session:
        assert reviewed_options(session, refs)["source_revision"] == old["source_revision"]
        assert read(session, refs).allowed


@pytest.mark.parametrize("withdraw_first", [False, True])
def test_postgres_fresh_consent_after_capacity_loss_serializes_with_withdrawal(s3_pg, monkeypatch, withdraw_first):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    _, other = prepare(s3_pg)
    with s3_pg() as session:
        submit(session, reviewed_options(session, refs))
        session.commit()
    with s3_pg() as session:
        capacity_cycle(session, monkeypatch, other)
        session.commit()
    with s3_pg() as session:
        fresh = reviewed_options(session, refs)
    accept = lambda session: submit(session, fresh).id
    revoke = lambda session: withdraw(session, refs).id
    functions = (revoke, accept) if withdraw_first else (accept, revoke)
    outcomes = ordered_wait(s3_pg, *functions)
    assert tuple(r[0] for r in outcomes) == (("ok", "denied") if withdraw_first else ("ok", "ok"))
    with s3_pg() as session:
        deny_old(session, refs, [fresh])
        assert all(r.withdrawn_at is not None for r in session.scalars(select(Authorization)))


def test_other_program_capacity_history_cannot_invalidate(progress_db, monkeypatch):
    factory, refs = progress_db, progress_db.refs
    _, other = prepare(factory)
    extra = add_other(factory, monkeypatch, other)
    end_other(factory, extra)
    with factory() as session:
        old = reviewed_options(session, refs)
        ident = submit(session, old).id
        session.commit()
    with factory() as session:
        if session.get(Organization, 2) is None:
            session.add(Organization(id=2, name="Independent Program", organization_type="music"))
            session.flush()
        if session.get(ClassroomProgram, 2) is None:
            proof = actor(session)
            s1.provision_program(session, actor=proof, organization_id=2, owner=proof)
        session.commit()
    with factory() as session:
        institutional(session, program_id=2)
        foreign = create(session, program_id=2).id
        extra = s2.assign_teacher(session, actor=actor(session), program_id=2,
            class_id=foreign, verifier_id=4).id
        session.commit()
    with factory() as session:
        s2.end_teaching(session, actor=actor(session), program_id=2, assignment_id=extra)
        for state in ("archived", "active"):
            s2.set_class_state(session, actor=actor(session), program_id=2, class_id=foreign, state=state)
        session.commit()
    with factory() as session:
        assert reviewed_options(session, refs)["source_revision"] == old["source_revision"]
        assert submit(session, old).id == ident and read(session, refs).allowed


def test_scheduled_larger_allowance_and_fallback_do_not_reset_capacity_history(progress_db, monkeypatch):
    factory, refs = progress_db, progress_db.refs
    monkeypatch.setenv("CLASSROOM_TEACHING_DIRECTOR_LIMIT", "1")
    now = factory.clock[0]
    with factory() as session:
        institutional(session, status="ended")
        trial = s2.start_trial(session, actor=actor(session), program_id=1).id
        other = create(session, name="Capacity across packages").id
        institutional(session, teacher_limit=2, starts_at=now + timedelta(minutes=1),
            ends_at=now + timedelta(minutes=2))
        session.commit()
    extra = add_other(factory, monkeypatch, other)
    end_other(factory, extra)
    with factory() as session:
        trial_form = reviewed_options(session, refs)
        submit(session, trial_form)
        session.commit()
    factory.clock[0] = now + timedelta(minutes=1)
    with factory() as session:
        deny_old(session, refs, [trial_form])
        package_form = reviewed_options(session, refs)
        submit(session, package_form)
        session.commit()
    extra = add_other(factory, monkeypatch, other)
    with factory() as session:
        assert s2.usage(session, 1) == (2, 2)
        assert s2.entitlement_decision(session, 1).allowed
        assert read(session, refs).allowed  # Both retained teachers fit this package.
        assert reviewed_options(session, refs)["source_revision"] == package_form["source_revision"]
    factory.clock[0] = now + timedelta(minutes=2)
    with factory() as session:
        decision = s2.entitlement_decision(session, 1)
        assert decision.entitlement_id == trial and decision.reason == "over_limit"
        deny_old(session, refs, [trial_form, package_form])
    end_other(factory, extra)
    with factory() as session:
        assert s2.entitlement_decision(session, 1).allowed
        deny_old(session, refs, [trial_form, package_form])
        assert submit(session, reviewed_options(session, refs)) and read(session, refs).allowed
