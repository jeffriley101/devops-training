"""Source reversals must never revive an acceptance or a previously issued form."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import classroom, classroom_s2 as s2, classroom_progress_authorization as service
from app.classroom_models import ClassroomProgressAuthorization as Authorization, ClassroomTeachingAssignment
from test_classroom_s1 import adult_pin_hash, classroom_db, actor
from test_classroom_s2 import s2_db, institutional, student
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web, student_client
from test_classroom_progress_routes import progress_web, form, url
from test_classroom_progress_authorization import progress_db, accept, read, withdraw
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2_postgres import s2_pg
from test_classroom_s3_postgres import s3_pg, setup_reporting


def reviewed_options(session, refs):
    proof = student(session)
    choice = service.choice(session, student=proof, program_id=1, class_id=refs.class_id)
    return dict(program_id=1, class_id=refs.class_id, notice_version=service.NOTICE_VERSION,
        opted_in=True, assignment_ids=choice.assignment_ids, previous=choice.revision,
        source_revision=choice.source_revision)


def reverse_sources(session, refs, kind):
    """Both halves share one timestamp and transaction, testing history rather than dates."""
    if kind in {"leave", "suspend", "remove"}:
        if kind == "leave":
            s2.leave_class(session, student=student(session), program_id=1, class_id=refs.class_id)
            s2.join_class(session, student=student(session), program_id=1, code="BAND2026")
        else:
            s2.suspend_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id, remove=kind == "remove")
            s2.reinstate_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id)
    elif kind == "entitlement":
        institutional(session, status="ended")
        institutional(session)
    elif kind == "archive":
        for state in ("archived", "active"):
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=refs.class_id, state=state)
    elif kind in {"assignment", "departure"}:
        added = s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=refs.class_id, verifier_id=3)
        s2.end_teaching(session, actor=actor(session, 3 if kind == "departure" else 1), program_id=1,
            assignment_id=added.id, reason="departed" if kind == "departure" else "revoked")
    else:
        raise AssertionError(kind)


def transition(factory, action, *, restore=False):
    """Use supported writers and separate transactions for the source transitions."""
    factory.clock[0] += timedelta(seconds=2)
    with factory() as session:
        class_id = factory.ids[0]
        if action in {"suspend", "remove"}:
            anchor = s2._anchor(session, class_id, 1)
            if restore:
                s2.reinstate_member(session, actor=actor(session), program_id=1,
                    class_id=class_id, membership_id=anchor.id)
            else:
                s2.suspend_member(session, actor=actor(session), program_id=1,
                    class_id=class_id, membership_id=anchor.id, remove=action == "remove")
        elif action == "entitlement":
            institutional(session, status="active" if restore else "ended")
        elif action == "archive":
            s2.set_class_state(session, actor=actor(session), program_id=1,
                class_id=class_id, state="active" if restore else "archived")
        else:
            raise AssertionError(action)
        session.commit()


@pytest.mark.parametrize("action", ["suspend", "entitlement", "archive"])
def test_original_stale_reauthorization_probe(progress_web, action):
    if action == "entitlement":
        # End the ordinary trial so removal really leaves no current entitlement.
        progress_web.clock[0] += timedelta(days=15)
        with progress_web() as session:
            institutional(session); session.commit()
    client = student_client()
    original = form(client)
    assert client.post(url(progress_web), data=original, follow_redirects=False).status_code == 303
    assert client.post(url(progress_web, "withdraw"), data={"csrf": original["csrf"]},
                       follow_redirects=False).status_code == 303
    reviewed = form(client)
    transition(progress_web, action)
    transition(progress_web, action, restore=True)
    stale = client.post(url(progress_web), data=reviewed, follow_redirects=False)
    assert stale.status_code == 409, f"{action}: stale form restored authorization (HTTP {stale.status_code})"
    with progress_web() as session:
        rows = list(session.scalars(select(Authorization).order_by(Authorization.id)))
        assert len(rows) == 1 and rows[0].withdrawn_at is not None
    fresh = form(client)
    assert client.post(url(progress_web), data=fresh, follow_redirects=False).status_code == 303
    with progress_web() as session:
        rows = list(session.scalars(select(Authorization).order_by(Authorization.id)))
        assert len(rows) == 2 and rows[0].withdrawn_at is not None and rows[1].withdrawn_at is None


def test_original_assignment_reversal_probe(progress_web):
    client = student_client(); data = form(client)
    assert client.post(url(progress_web), data=data, follow_redirects=False).status_code == 303
    with progress_web() as session:
        added = s2.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=progress_web.ids[0], verifier_id=3)
        added_id = added.id
        session.commit()
    assert "inactive" in client.get("/classroom/reporting").text
    with progress_web() as session:
        s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=added_id)
        session.commit()
    with progress_web() as session:
        with pytest.raises(classroom.ClassroomDenied, match=service.UNAVAILABLE):
            service.authorize_boundary(session, actor=actor(session, 2), program_id=1,
                class_id=progress_web.ids[0], profile_id=1)
    assert "inactive" in client.get("/classroom/reporting").text
    assert client.post(url(progress_web), data=form(client), follow_redirects=False).status_code == 303
    with progress_web() as session:
        rows = list(session.scalars(select(Authorization).order_by(Authorization.id)))
        assert len(rows) == 2 and rows[0].withdrawal_reason == "replaced"
        assert service.authorize_boundary(session, actor=actor(session, 2), program_id=1,
            class_id=progress_web.ids[0], profile_id=1).allowed
        with pytest.raises(classroom.ClassroomDenied):
            service.authorize_boundary(session, actor=actor(session, 3), program_id=1,
                class_id=progress_web.ids[0], profile_id=1)


@pytest.mark.parametrize("kind", ["leave", "suspend", "remove", "entitlement", "archive", "assignment", "departure"])
def test_initial_form_and_saved_acceptance_use_the_same_durable_revision(progress_db, kind):
    refs = progress_db.refs
    with progress_db() as session:
        original = reviewed_options(session, refs)
        assert session.scalar(select(Authorization)) is None
        session.commit()
    progress_db.clock[0] += timedelta(seconds=2)
    with progress_db() as session:
        reverse_sources(session, refs, kind); session.commit()
    with progress_db() as session:
        with pytest.raises(classroom.ClassroomDenied):
            service.accept(session, student=student(session), **original)
        assert session.scalar(select(Authorization)) is None
        first = accept(session, refs)
        first_id, first_revision = first.id, first.source_revision
        assert first_revision != original["source_revision"] and read(session, refs).allowed
        session.commit()
    progress_db.clock[0] += timedelta(seconds=2)
    with progress_db() as session:
        # Archival revokes the join code; reissue only when a leave/rejoin uses it.
        reverse_sources(session, refs, kind); session.commit()
    with progress_db() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        retained = session.get(Authorization, first_id)
        assert retained.source_revision == first_revision and retained.withdrawn_at is None
        renewed = accept(session, refs)
        assert renewed.id != first_id and renewed.source_revision != first_revision
        assert session.get(Authorization, first_id).withdrawal_reason == "replaced"
        assert read(session, refs).allowed


def test_unrelated_sources_leave_reviewed_form_and_acceptance_valid(progress_web):
    from app import classroom_progress_routes as routes
    from test_classroom_s2 import create
    client = student_client(); original = form(client)
    with progress_web() as session:
        second = s2.authenticate_student(session, woodchuck_id="WC-SECOND", pin="2468")
        other_member = s2.join_class(session, student=second, program_id=1, code="FIRST2026")
        other_class = create(session, name="Unrelated Class")
        extra_teacher = s2.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=other_class.id, verifier_id=3)
        session.commit()
    progress_web.clock[0] += timedelta(seconds=2)
    with progress_web() as session:
        s2.suspend_member(session, actor=actor(session), program_id=1, class_id=progress_web.ids[0],
            membership_id=other_member.id)
        s2.reinstate_member(session, actor=actor(session), program_id=1, class_id=progress_web.ids[0],
            membership_id=other_member.id)
        s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=extra_teacher.id)
        for state in ("archived", "active"):
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=other_class.id, state=state)
        session.commit()
    fresh = form(client)
    assert routes._signer().loads(original["notice_token"])[7] == routes._signer().loads(fresh["notice_token"])[7]
    assert client.post(url(progress_web), data=original, follow_redirects=False).status_code == 303
    with progress_web() as session:
        assert service.authorize_boundary(session, actor=actor(session, 2), program_id=1,
            class_id=progress_web.ids[0], profile_id=1).allowed


def test_signed_revision_must_be_present_current_and_untampered(progress_web):
    from app import classroom_progress_routes as routes
    client = student_client(); original = form(client)
    payload = routes._signer().loads(original["notice_token"])
    for token in (original["notice_token"] + "x", routes._signer().dumps(payload[:7]),
                  routes._signer().dumps(payload[:7] + ["0" * 64])):
        assert client.post(url(progress_web), data=original | {"notice_token": token},
                           follow_redirects=False).status_code == 409
    with progress_web() as session:
        assert session.scalar(select(Authorization)) is None


@pytest.mark.parametrize("kind", ["suspend", "entitlement", "archive", "assignment"])
@pytest.mark.parametrize("mutation_first", [False, True])
def test_postgres_initial_acceptance_serializes_with_source_reversal(s3_pg, monkeypatch, kind, mutation_first):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    with s3_pg() as session:
        original = reviewed_options(session, refs)
        assert session.scalar(select(Authorization)) is None
    s3_pg.reporting_clock[0] += timedelta(seconds=2)
    def mutate(session):
        reverse_sources(session, refs, kind)
        return "sources changed"
    def submit(session):
        return service.accept(session, student=student(session), **original).id
    functions = (mutate, submit) if mutation_first else (submit, mutate)
    outcomes = ordered_wait(s3_pg, *functions)
    assert tuple(result[0] for result in outcomes) == (("ok", "denied") if mutation_first else ("ok", "ok"))
    with s3_pg() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        rows = list(session.scalars(select(Authorization)))
        assert len(rows) == (0 if mutation_first else 1)
        assert accept(session, refs).source_revision != original["source_revision"]
        assert read(session, refs).allowed


@pytest.mark.parametrize("kind", ["suspend", "entitlement", "archive", "assignment"])
def test_postgres_source_mutation_rollback_does_not_invalidate_review(s3_pg, monkeypatch, kind):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    with s3_pg() as session:
        original = reviewed_options(session, refs)
    s3_pg.reporting_clock[0] += timedelta(seconds=2)
    def mutate(session):
        reverse_sources(session, refs, kind)
        return "rolled back"
    outcomes = ordered_wait(s3_pg, mutate,
        lambda session: service.accept(session, student=student(session), **original).id, rollback_first=True)
    assert tuple(result[0] for result in outcomes) == ("ok", "ok")
    with s3_pg() as session:
        assert read(session, refs).allowed
        assert session.scalar(select(Authorization)).source_revision == original["source_revision"]


@pytest.mark.parametrize("withdraw_first", [False, True])
def test_postgres_reacceptance_and_withdrawal_do_not_resurrect_consent(s3_pg, monkeypatch, withdraw_first):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    with s3_pg() as session:
        accept(session, refs); session.commit()
    s3_pg.reporting_clock[0] += timedelta(seconds=2)
    with s3_pg() as session:
        reverse_sources(session, refs, "assignment"); session.commit()
    with s3_pg() as session:
        reviewed = reviewed_options(session, refs)
    submit = lambda session: service.accept(session, student=student(session), **reviewed).id
    revoke = lambda session: withdraw(session, refs).id
    functions = (revoke, submit) if withdraw_first else (submit, revoke)
    outcomes = ordered_wait(s3_pg, *functions)
    assert tuple(item[0] for item in outcomes) == (("ok", "denied") if withdraw_first else ("ok", "ok"))
    with s3_pg() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        rows = list(session.scalars(select(Authorization)))
        assert len(rows) == (1 if withdraw_first else 2)
        assert all(row.withdrawn_at is not None for row in rows)


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_postgres_source_decisions_refuse_stale_snapshot_isolation(s3_pg, monkeypatch, isolation):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    with s3_pg() as session:
        reviewed = reviewed_options(session, refs)
    with s3_pg.kw["bind"].connect().execution_options(isolation_level=isolation) as connection:
        with s3_pg(bind=connection) as session:
            with pytest.raises(classroom.ClassroomDenied, match="READ COMMITTED"):
                service.accept(session, student=student(session), **reviewed)
