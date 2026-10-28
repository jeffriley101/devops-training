"""Explicit consent, retained-source isolation and immediate withdrawal on both databases."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import classroom, classroom_reporting as boundary, classroom_s2 as s2, memberships
from app import classroom_progress_authorization as service, classroom_chart_sharing as sharing
from app.age_models import AccountPrivacy
from app.age_privacy import utc
from app.child_models import ConsentEvidence
from app.classroom_models import (
    ClassroomProgressAuthorization as Authorization, ClassroomProgressRecipient as Recipient,
    ClassroomReportingPeriod, ClassroomEntitlement, ClassroomTeachingAssignment,
)
from app.models import PracticeChart, WoodchuckProfile
from app.feature_access import can_use_feature
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW, actor, student, create, code, join, institutional
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2_postgres import s2_pg
from test_classroom_s3_postgres import s3_pg, setup_reporting


@pytest.fixture(params=["sqlite", "postgresql"])
def progress_db(request, monkeypatch):
    factory = request.getfixturevalue("s2_db" if request.param == "sqlite" else "s3_pg")
    monkeypatch.setenv("CLASSROOM_S3_ENABLED", "1")
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    monkeypatch.setenv("CLASSROOM_DIRECTOR_ENABLED", "1")
    clock = [NOW]
    monkeypatch.setattr(s2, "_now", lambda: clock[0])
    monkeypatch.setattr(boundary, "_now", lambda: clock[0])
    monkeypatch.setattr(memberships, "clock", lambda: clock[0])
    factory.clock = clock
    factory.refs = setup_reporting(factory, granted=False)
    return factory


def accept(session, refs, **changes):
    proof = student(session)
    reviewed = service.choice(session, student=proof, program_id=1, class_id=refs.class_id)
    options = dict(student=proof, program_id=1, class_id=refs.class_id,
        notice_version=service.NOTICE_VERSION, opted_in=True, assignment_ids=(refs.assignment_id,),
        previous=reviewed.revision, source_revision=reviewed.source_revision)
    return service.accept(session, **(options | changes))


def read(session, refs, **changes):
    options = dict(actor=actor(session, 2), program_id=1, class_id=refs.class_id, profile_id=1)
    return service.authorize_boundary(session, **(options | changes))


def withdraw(session, refs):
    return service.withdraw(session, student=student(session), program_id=1, class_id=refs.class_id)


def test_explicit_acceptance_evidence_and_withdrawal(progress_db):
    refs = progress_db.refs
    with progress_db() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        for changes in ({"opted_in": False}, {"opted_in": "yes"}, {"notice_version": "old"},
                        {"assignment_ids": ()}, {"student": None}):
            with pytest.raises(classroom.ClassroomDenied):
                accept(session, refs, **changes)
        assert session.scalar(select(Authorization)) is None
        # Legacy boundary-only acceptance cannot satisfy the new notice.
        boundary.grant_reporting(session, student=student(session), program_id=1, class_id=refs.class_id,
            scope_version=boundary.SCOPE_VERSION, notice_version=boundary.NOTICE_VERSION)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        row = accept(session, refs)
        ident, timestamp = row.id, row.accepted_at
        assert row.profile_id == row.accepted_by_profile_id == 1
        assert row.notice_digest == service.NOTICE_DIGEST and row.notice_version == service.NOTICE_VERSION
        assert row.acceptance_session_version == session.get(WoodchuckProfile, 1).session_version
        assert session.scalar(select(Recipient.assignment_id)) == refs.assignment_id
        assert accept(session, refs).id == ident
        assert read(session, refs).allowed
        assert read(session, refs).starts_at == timestamp
        for changes in ({"fields": ("practice_minutes",)}, {"operation": "export"},
                        {"profile_id": 999}, {"program_id": 2}, {"class_id": 999},
                        {"actor": actor(session, 1)}):
            with pytest.raises(classroom.ClassroomDenied):
                read(session, refs, **changes)
        progress_db.clock[0] += timedelta(seconds=1)
        withdraw(session, refs)
        first_withdrawal = row.withdrawn_at
        assert withdraw(session, refs) is None
        assert utc(row.withdrawn_at) == utc(first_withdrawal) and row.withdrawn_by_profile_id == 1
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        assert session.scalar(select(ClassroomReportingPeriod)).ended_at is None
        assert s2.contribution_active(session, program_id=1, class_id=refs.class_id, profile_id=1)
        renewed = accept(session, refs)
        assert renewed.id != ident and read(session, refs).starts_at == utc(renewed.accepted_at)


@pytest.mark.parametrize("loss", ["leave", "suspend", "expire", "replacement", "archive"])
def test_source_loss_never_silently_restores_and_still_allows_withdrawal(progress_db, loss):
    refs = progress_db.refs
    with progress_db() as session:
        accept(session, refs)
        session.commit()
        progress_db.clock[0] += timedelta(seconds=2)
        if loss == "leave":
            s2.leave_class(session, student=student(session), program_id=1, class_id=refs.class_id)
        elif loss == "suspend":
            s2.suspend_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id)
        elif loss == "expire":
            progress_db.clock[0] = session.get(ClassroomEntitlement, refs.entitlement_id).ends_at
            progress_db.clock[0] = utc(progress_db.clock[0])
        elif loss == "replacement":
            s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
            s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=refs.class_id, verifier_id=3)
        else:
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=refs.class_id, state="archived")
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        # The withdrawal path works during the actual loss, without renewing it.
        with session.begin_nested() as checkpoint:
            withdraw(session, refs)
            assert session.scalar(select(Authorization)).withdrawn_at is not None
            checkpoint.rollback()
        progress_db.clock[0] += timedelta(seconds=2)
        if loss == "leave":
            join(session)
        elif loss == "suspend":
            s2.reinstate_member(session, actor=actor(session), program_id=1, class_id=refs.class_id,
                membership_id=refs.membership_id)
        elif loss == "expire":
            institutional(session, starts_at=progress_db.clock[0], ends_at=progress_db.clock[0] + timedelta(days=30))
        elif loss == "replacement":
            with pytest.raises(classroom.ClassroomDenied):
                read(session, refs, actor=actor(session, 3))
        else:
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=refs.class_id, state="active")
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        withdraw(session, refs)
        assert session.scalar(select(Authorization)).withdrawn_at is not None


@pytest.mark.parametrize("restriction", ["unknown", "under13", "consent_link", "inactive"])
def test_account_restrictions_block_acceptance_and_saved_authorization(progress_db, restriction):
    refs = progress_db.refs
    with progress_db() as session:
        accept(session, refs)
        rule = session.get(AccountPrivacy, 1)
        if restriction == "inactive":
            session.get(WoodchuckProfile, 1).status = "deleted"
        elif restriction == "consent_link":
            evidence = ConsentEvidence(profile_id=1, notice_version="synthetic", notice_sha256="0" * 64,
                approved_at=NOW, confirmed_at=NOW)
            session.add(evidence); session.flush()
            rule.consent_id = evidence.id
        else:
            rule.age_band = restriction
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        with pytest.raises(classroom.ClassroomDenied):
            accept(session, refs)
        if restriction != "inactive":
            withdraw(session, refs)
            assert session.scalar(select(Authorization)).withdrawn_at is not None


def test_pchart_records_and_permissions_are_independent(progress_db):
    refs = progress_db.refs
    with progress_db() as session:
        shared = PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=12, instrument="Trumpet",
            note="shared", created_at=NOW)
        private = PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=999, instrument="Trumpet",
            note="private", created_at=NOW)
        session.add_all([shared, private]); session.flush()
        sharing.set_sharing(session, student=student(session), chart_id=shared.id, assignment_ids=[refs.assignment_id])
        session.commit()
        def charts():
            return sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
        before = charts()
        assert len(before) == 1
        assert can_use_feature(session, 1, "practice_insights")
        accept(session, refs)
        assert charts() == before
        withdraw(session, refs)
        assert charts() == before
        assert can_use_feature(session, 1, "practice_insights")
        assert session.get(PracticeChart, private.id).minutes == 999
        assert session.get(PracticeChart, shared.id).minutes == 12


def test_authorized_class_and_student_never_authorize_another(progress_db):
    refs = progress_db.refs
    with progress_db() as session:
        accept(session, refs)
        other_class = create(session, name="Other Class")
        code(session, other_class.id, preferred="OTHER2026")
        s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=other_class.id, verifier_id=2)
        join(session, "OTHER2026")
        session.add(WoodchuckProfile(id=2, woodchuck_id="WC-SECOND", display_name="Other Student",
            pin_hash=session.get(WoodchuckProfile, 1).pin_hash, instrument="Flute", level="Beginner", goal="Practice"))
        session.flush()
        session.add(AccountPrivacy(profile_id=2, age_band="adult", declared_at=NOW, public_from=NOW))
        session.commit()
        other = lambda: s2.authenticate_student(session, woodchuck_id="WC-SECOND", pin="2468")
        s2.join_class(session, student=other(), program_id=1, code="BAND2026")
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs, class_id=other_class.id)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs, profile_id=2)
        assert service.withdraw(session, student=other(), program_id=1, class_id=refs.class_id) is None
        assert read(session, refs).allowed
        proof = other()
        reviewed = service.choice(session, student=proof, program_id=1, class_id=refs.class_id)
        service.accept(session, student=proof, program_id=1, class_id=refs.class_id,
            opted_in=True, notice_version=service.NOTICE_VERSION, assignment_ids=(refs.assignment_id,),
            previous=(None, None), source_revision=reviewed.source_revision)
        assert read(session, refs, profile_id=2).allowed
        withdraw(session, refs)
        assert read(session, refs, profile_id=2).allowed
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)


@pytest.mark.parametrize("withdraw_first", [False, True])
def test_postgres_withdrawal_serializes_with_authorization(s3_pg, monkeypatch, withdraw_first):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    refs = setup_reporting(s3_pg, granted=False)
    with s3_pg() as session:
        accept(session, refs); session.commit()
    revoke = lambda session: withdraw(session, refs).id
    check = lambda session: read(session, refs).allowed
    functions = (revoke, check) if withdraw_first else (check, revoke)
    results = ordered_wait(s3_pg, *functions)
    assert tuple(item[0] for item in results) == (("ok", "denied") if withdraw_first else ("ok", "ok"))
    with s3_pg() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
