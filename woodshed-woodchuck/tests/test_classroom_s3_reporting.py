"""Synthetic internal boundary proofs. No student-data field contract is enabled."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select

from app import classroom, classroom_s2 as s2, classroom_reporting as reporting
from app.age_models import AccountPrivacy
from app.age_privacy import utc
from app.classroom_models import (
    ClassroomReportingPeriod, ClassroomS2AuditEvent, ClassroomS3AuditEvent, ClassroomTeachingAssignment,
)
from app.models import PracticeChart, StudentVerifierConnection, WoodchuckProfile
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW, ready, join, student, institutional, code, create


@pytest.fixture
def report_db(s2_db, monkeypatch):
    monkeypatch.setenv("CLASSROOM_S3_ENABLED", "1")
    clock = [NOW]
    monkeypatch.setattr(reporting, "_now", lambda: clock[0])
    monkeypatch.setattr(s2, "_now", lambda: clock[0])
    return s2_db, clock


def setup(session, *, program_id=1, preferred="BAND2026", teacher=2):
    room, _, _ = ready(session, program_id=program_id, preferred=preferred)
    member = join(session, visible=preferred, program_id=program_id)
    assignment = s2.assign_teacher(session, actor=actor(session), program_id=program_id,
                                   class_id=room.id, verifier_id=teacher)
    return room.id, member.id, assignment.id


def grant(session, class_id, *, program_id=1, reauthorize=False, **kwargs):
    return (reporting.reauthorize_reporting if reauthorize else reporting.grant_reporting)(
        session, student=student(session), program_id=program_id, class_id=class_id,
        scope_version=kwargs.get("scope_version", reporting.SCOPE_VERSION),
        notice_version=kwargs.get("notice_version", reporting.NOTICE_VERSION))


def read(session, class_id, *, adult_id=2, program_id=1, profile_id=1, **kwargs):
    return reporting.authorize_reporting(session, actor=actor(session, adult_id),
        program_id=program_id, class_id=class_id, profile_id=profile_id, **kwargs)


def withdraw(session, class_id, *, program_id=1):
    return reporting.withdraw_reporting(session, student=student(session),
                                         program_id=program_id, class_id=class_id)


def chart(session, day, submitted, *, profile_id=1):
    row = PracticeChart(profile_id=profile_id, practice_date=day, minutes=17,
        instrument="Trumpet", note="PRIVATE NOTE MUST NOT BE READ", source="pristine",
        detected_playing_seconds=1020, practice_details=[], created_at=submitted)
    session.add(row)
    session.flush()
    return row.id


def history(session, class_id, chart_id, *, program_id=1, adult_id=2):
    return reporting.chart_history_decision(session, actor=actor(session, adult_id),
        program_id=program_id, class_id=class_id, profile_id=1, chart_id=chart_id)


def test_grant_withdraw_regrant_preserves_intervals_and_tools(report_db):
    from app.classroom_capabilities import capability_decision
    factory, clock = report_db
    with factory() as session:
        class_id, member_id, _ = setup(session)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)
        first = grant(session, class_id)
        assert first.membership_id == member_id
        assert read(session, class_id).allowed
        with pytest.raises(classroom.ClassroomDenied):
            grant(session, class_id)
        clock[0] += timedelta(days=2)
        withdraw(session, class_id)
        ended = utc(first.ended_at)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)
        assert s2.contribution_active(session, program_id=1, class_id=class_id, profile_id=1)
        assert capability_decision(session, 1, "pristine_use").allowed
        assert capability_decision(session, 1, "personal_progress_save").allowed
        assert withdraw(session, class_id) is None  # no duplicate audit
        clock[0] += timedelta(days=2)
        second = grant(session, class_id, reauthorize=True)
        assert second.id != first.id and utc(second.starts_at) > ended
        assert read(session, class_id).starts_at == utc(second.starts_at)
        assert [a.action for a in session.scalars(select(ClassroomS3AuditEvent).order_by(
            ClassroomS3AuditEvent.id))] == ["reporting_granted", "reporting_withdrawn", "reporting_reauthorized"]


@pytest.mark.parametrize("fields,operation", [(("student_id",), reporting.OPERATION),
    (("practice_date",), reporting.OPERATION), (("minutes",), reporting.OPERATION),
    (("parent_email",), reporting.OPERATION), ((), "export"), ((), "read"), ((), "verify")])
def test_all_data_fields_exports_unapproved_operations_denied(report_db, fields, operation):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        grant(session, class_id)
        with pytest.raises(classroom.ClassroomDenied, match="^Classroom reporting unavailable[.]$"):
            read(session, class_id, fields=fields, operation=operation)


@pytest.mark.parametrize("key,value", [("scope_version", "broad-v1"),
    ("notice_version", "unapproved-v2"), ("scope_version", "")])
def test_unapproved_scope_or_notice_cannot_create_grant(report_db, key, value):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        with pytest.raises(classroom.ClassroomDenied):
            grant(session, class_id, **{key: value})
        assert session.scalar(select(func.count()).select_from(ClassroomReportingPeriod)) == 0


@pytest.mark.parametrize("role", [None, "admin", "billing", "code_manager", "head_director"])
def test_management_roles_and_free_connection_do_not_authorize(report_db, role):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        grant(session, class_id)
        if role:
            classroom.grant_role(session, actor=actor(session), program_id=1, verifier_id=3, role=role)
        session.add(StudentVerifierConnection(profile_id=1, verifier_id=3, role="band_director", status="accepted"))
        session.flush()
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id, adult_id=3)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id, adult_id=1)  # canonical Owner
        assert read(session, class_id).allowed


def test_exact_student_class_program_and_ownership_transfer(report_db):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        second = create(session, name="Unrelated")
        grant(session, class_id)
        for kwargs, selected in [({}, second.id), ({"profile_id": 99999}, class_id),
                                 ({"program_id": 2}, class_id)]:
            with pytest.raises(classroom.ClassroomDenied):
                read(session, selected, **kwargs)
        transfer = classroom.propose_transfer(session, actor=actor(session), program_id=1, recipient_id=3)
        classroom.accept_transfer(session, actor=actor(session, 3), program_id=1, transfer_id=transfer.id)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id, adult_id=3)
        assert read(session, class_id).allowed


@pytest.mark.parametrize("change", ["inactive", "unknown", "under13", "consent_link"])
def test_underlying_account_and_child_authorization_authoritative(report_db, change):
    from app.child_models import ConsentEvidence
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        grant(session, class_id)
        rule = session.get(AccountPrivacy, 1)
        if change == "inactive":
            session.get(WoodchuckProfile, 1).status = "deleted"
        elif change == "consent_link":
            evidence = ConsentEvidence(profile_id=1, notice_version="synthetic", notice_sha256="0" * 64,
                approved_at=clock[0], confirmed_at=clock[0])
            session.add(evidence)
            session.flush()
            rule.consent_id = evidence.id
        else:
            rule.age_band = change
        session.flush()
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)


def test_valid_child_account_consent_does_not_infer_class_reporting(report_db, monkeypatch):
    from app import child_authorization
    from app.child_models import ConsentEvidence
    from app.age_privacy import eligible
    monkeypatch.setattr(child_authorization, "under13_available", lambda: True)
    monkeypatch.setattr(child_authorization, "notice_policy", lambda: ("synthetic", "", "a" * 64))
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        version, _, digest = child_authorization.notice_policy()
        evidence = ConsentEvidence(profile_id=1, parent_email="parent@example.test",
                                   notice_version=version, notice_sha256=digest,
                                   approved_at=clock[0], confirmed_at=clock[0])
        session.add(evidence)
        session.flush()
        rule = session.get(AccountPrivacy, 1)
        rule.consent_id, rule.age_band = evidence.id, "under13"
        session.flush()
        assert eligible(session, 1)
        with pytest.raises(classroom.ClassroomDenied):
            grant(session, class_id)


@pytest.mark.parametrize("reason", ["departed", "revoked"])
def test_departed_or_revoked_teacher_loses_reads(report_db, reason):
    factory, _ = report_db
    with factory() as session:
        class_id, _, assignment_id = setup(session)
        grant(session, class_id)
        classroom.end_teaching(session, actor=actor(session, 2 if reason == "departed" else 1),
            program_id=1, assignment_id=assignment_id, reason=reason)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)


def test_occurrence_submission_and_ambiguous_boundary_day(report_db):
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        clock[0] += timedelta(days=1)
        granted = grant(session, class_id)
        start = utc(granted.starts_at)
        day = start.astimezone(reporting.CENTRAL).date()
        clock[0] += timedelta(days=4)
        before = chart(session, day - timedelta(days=1), clock[0])
        ambiguous = chart(session, day, clock[0])
        good = chart(session, day + timedelta(days=1), start + timedelta(days=2))
        bad_submission = chart(session, day + timedelta(days=1), start - timedelta(seconds=1))
        future = chart(session, clock[0].date() + timedelta(days=2), clock[0])
        for cid in (before, ambiguous, bad_submission, future):
            assert not history(session, class_id, cid).allowed
        assert history(session, class_id, good).allowed


def test_reauthorization_excludes_old_interval_and_unauthorized_gap(report_db):
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        first = grant(session, class_id)
        clock[0] += timedelta(days=3)
        old = chart(session, (clock[0] - timedelta(days=1)).date(), clock[0])
        assert history(session, class_id, old).allowed
        withdraw(session, class_id)
        clock[0] += timedelta(days=3)
        gap = chart(session, (clock[0] - timedelta(days=1)).date(), clock[0])
        grant(session, class_id, reauthorize=True)
        clock[0] += timedelta(days=3)
        new = chart(session, (clock[0] - timedelta(days=1)).date(), clock[0])
        assert not history(session, class_id, old).allowed
        assert not history(session, class_id, gap).allowed
        assert history(session, class_id, new).allowed
        assert first.ended_at is not None


@pytest.mark.parametrize("local_start", [(2026, 11, 1), (2027, 3, 14)])
def test_exact_midnight_boundaries_use_chicago_calendar_across_dst(report_db, local_start):
    factory, clock = report_db
    midnight = datetime(*local_start, tzinfo=reporting.CENTRAL)
    clock[0] = midnight.astimezone(timezone.utc)
    with factory() as session:
        class_id, _, _ = setup(session)
        grant(session, class_id)
        cid = chart(session, midnight.date(), clock[0] + timedelta(hours=12))
        next_midnight = midnight + timedelta(days=1)
        clock[0] = next_midnight.astimezone(timezone.utc) - timedelta(microseconds=1)
        assert not history(session, class_id, cid).allowed
        clock[0] += timedelta(microseconds=1)
        assert history(session, class_id, cid).allowed


@pytest.mark.parametrize("change", ["leave", "suspend", "archive", "entitlement"])
def test_source_gap_requires_explicit_new_period(report_db, change):
    factory, clock = report_db
    with factory() as session:
        class_id, membership_id, _ = setup(session)
        first = grant(session, class_id)
        clock[0] += timedelta(days=1)
        if change == "leave":
            s2.leave_class(session, student=student(session), program_id=1, class_id=class_id)
        elif change == "suspend":
            s2.suspend_member(session, actor=actor(session), program_id=1,
                              class_id=class_id, membership_id=membership_id)
        elif change == "archive":
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=class_id, state="archived")
        else:
            clock[0] += timedelta(days=14)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)
        clock[0] += timedelta(days=1)
        if change == "leave":
            join(session)
        elif change == "suspend":
            s2.reinstate_member(session, actor=actor(session), program_id=1,
                                class_id=class_id, membership_id=membership_id)
        elif change == "archive":
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=class_id, state="active")
        else:
            institutional(session, starts_at=clock[0], ends_at=clock[0] + timedelta(days=30))
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)
        new = grant(session, class_id, reauthorize=True)
        assert new.id != first.id and first.ended_reason == "source_changed"
        assert read(session, class_id).starts_at == utc(new.starts_at)


def test_archive_same_timestamp_still_invalidates_reporting(report_db):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        grant(session, class_id)
        for state in ("archived", "active"):
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=class_id, state=state)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)
        grant(session, class_id, reauthorize=True)
        assert read(session, class_id).allowed


def test_codirector_assignment_history_starts_later(report_db):
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        grant(session, class_id)
        clock[0] += timedelta(days=3)
        old = chart(session, (clock[0] - timedelta(days=1)).date(), clock[0])
        new_assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
                                         class_id=class_id, verifier_id=3)
        # Existing S1 uses its real server clock, so set synthetic evidence to
        # the test clock when modeling an assignment several days in the future.
        new_assignment.starts_at = clock[0]
        session.flush()
        clock[0] += timedelta(days=3)
        assert history(session, class_id, old).allowed
        assert not history(session, class_id, old, adult_id=3).allowed


def test_multiclass_dedup_keeps_unavailable_and_free_scope_separate(report_db):
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        second = create(session, name="Second Class")
        code(session, second.id, "SECOND99")
        join(session, visible="SECOND99")
        s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=second.id, verifier_id=2)
        grant(session, class_id)
        grant(session, second.id)
        session.add(StudentVerifierConnection(profile_id=1, verifier_id=2, role="band_director", status="accepted"))
        clock[0] += timedelta(days=3)
        cid = chart(session, (clock[0] - timedelta(days=1)).date(), clock[0])
        decisions = [history(session, room, cid) for room in (class_id, second.id)]
        assert all(d.allowed for d in decisions)
        assert len(reporting.deduplicate_history_decisions(session, decisions, actor=actor(session, 2))) == 1
        withheld = history(session, class_id, 99999)
        combined = reporting.deduplicate_history_decisions(session, decisions + [withheld], actor=actor(session, 2))
        assert len(combined) == 2 and combined[1].allowed is False
        with pytest.raises(classroom.ClassroomDenied):
            reporting.deduplicate_history_decisions(session, decisions + [{"connected_student": 1}], actor=actor(session, 2))
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            reporting.deduplicate_history_decisions(session, decisions, actor=actor(session, 2))


def test_dedup_rechecks_withdrawal_in_same_transaction(report_db):
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        grant(session, class_id)
        clock[0] += timedelta(days=3)
        cid = chart(session, (clock[0] - timedelta(days=1)).date(), clock[0])
        decision = history(session, class_id, cid)
        assert decision.allowed
        withdraw(session, class_id)
        with pytest.raises(classroom.ClassroomDenied):
            reporting.deduplicate_history_decisions(session, [decision], actor=actor(session, 2))


def test_program_a_cannot_rescue_program_b_reporting(report_db):
    factory, _ = report_db
    with factory() as session:
        class_a, _, _ = setup(session)
        grant(session, class_a)
        session.commit()
        class_b, _, _ = setup(session, program_id=2, preferred="PROGRAMB")
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_b, program_id=2)
        grant(session, class_b, program_id=2)
        assert read(session, class_b, program_id=2).allowed
        withdraw(session, class_b, program_id=2)
        session.commit()
        assert read(session, class_a).allowed
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_b, program_id=2)


def test_mutation_and_audit_rollback_together(report_db):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        session.commit()
        grant(session, class_id)
        session.rollback()
        assert session.scalar(select(func.count()).select_from(ClassroomReportingPeriod)) == 0
        assert session.scalar(select(func.count()).select_from(ClassroomS3AuditEvent)) == 0
        grant(session, class_id)
        session.commit()
        withdraw(session, class_id)
        session.rollback()
        assert read(session, class_id).allowed
        events = list(session.scalars(select(ClassroomS3AuditEvent)))
        assert len(events) == 1 and events[0].scope_version == reporting.SCOPE_VERSION
        assert events[0].notice_version == reporting.NOTICE_VERSION
        assert set(ClassroomS3AuditEvent.__table__.columns.keys()).isdisjoint(
            {"pin", "parent_email", "token", "note", "payload", "credential_hash"})


def test_cross_program_audit_watermark_fails_closed(report_db):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        period_id = grant(session, class_id).id
        session.commit()
        ready(session, program_id=2, preferred="PROGRAMB")
        foreign_audit = session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(
            ClassroomS2AuditEvent.program_id == 2))
        session.commit()
        session.get(ClassroomReportingPeriod, period_id).source_audit_id = foreign_audit
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)


def test_audit_failure_cannot_leave_committable_grant(report_db):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        session.commit()

        def fail(mapper, connection, target):
            raise RuntimeError("synthetic audit failure")

        event.listen(ClassroomS3AuditEvent, "before_insert", fail)
        try:
            with pytest.raises(RuntimeError, match="synthetic audit failure"):
                grant(session, class_id)
        finally:
            event.remove(ClassroomS3AuditEvent, "before_insert", fail)
        session.commit()
        assert session.scalar(select(func.count()).select_from(ClassroomReportingPeriod)) == 0


def test_reauthorization_rollback_preserves_withdrawn_interval(report_db):
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        first = grant(session, class_id)
        clock[0] += timedelta(days=1)
        withdraw(session, class_id)
        session.commit()
        end = utc(first.ended_at)
        clock[0] += timedelta(days=1)
        grant(session, class_id, reauthorize=True)
        session.rollback()
        rows = list(session.scalars(select(ClassroomReportingPeriod)))
        assert len(rows) == 1 and utc(rows[0].ended_at) == end
        assert list(session.scalars(select(ClassroomS3AuditEvent.action).order_by(
            ClassroomS3AuditEvent.id))) == ["reporting_granted", "reporting_withdrawn"]
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)


def test_disabled_and_forged_authentication_denied(report_db, monkeypatch):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        with pytest.raises(classroom.ClassroomDenied):
            reporting.grant_reporting(session, student=1, program_id=1, class_id=class_id,
                scope_version=reporting.SCOPE_VERSION, notice_version=reporting.NOTICE_VERSION)
        grant(session, class_id)
        with pytest.raises(classroom.ClassroomDenied):
            reporting.authorize_reporting(session, actor=2, program_id=1, class_id=class_id, profile_id=1)
        monkeypatch.setenv("CLASSROOM_S3_ENABLED", "0")
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)


def test_another_class_assignment_cannot_authorize_fully_granted_class(report_db):
    factory, _ = report_db
    with factory() as session:
        first, _, _ = setup(session)
        second = create(session, name="Separate Class")
        code(session, second.id, "SEPARATE8")
        join(session, visible="SEPARATE8")
        grant(session, first)
        grant(session, second.id)
        assert read(session, first).allowed
        with pytest.raises(classroom.ClassroomDenied):
            read(session, second.id)


def test_package_round_trip_at_same_instant_needs_new_authorization(report_db):
    factory, _ = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        first = grant(session, class_id)
        # Replacing the trial and then falling back to that exact trial must
        # not silently revive its original reporting authorization.
        institutional(session)
        institutional(session, status="ended")
        assert s2.entitlement_decision(session, 1).entitlement_id == first.entitlement_id
        with pytest.raises(classroom.ClassroomDenied):
            read(session, class_id)
        second = grant(session, class_id, reauthorize=True)
        assert first.ended_reason == "source_changed"
        assert utc(first.ended_at) == utc(second.starts_at)
        assert read(session, class_id).period_id == second.id


@pytest.mark.parametrize("operation", ["withdraw", "reauthorize"])
def test_audit_failure_rolls_back_closure_and_replacement(report_db, operation):
    factory, clock = report_db
    with factory() as session:
        class_id, _, _ = setup(session)
        first = grant(session, class_id)
        session.commit()
        clock[0] += timedelta(days=1)
        if operation == "reauthorize":
            institutional(session)
            session.commit()

        def fail(mapper, connection, target):
            # Fail the final reauthorization audit after the old period has
            # already been closed, flushed, and audited inside the savepoint.
            expected = "reporting_withdrawn" if operation == "withdraw" else "reporting_reauthorized"
            if target.action == expected:
                raise RuntimeError("synthetic final audit failure")

        event.listen(ClassroomS3AuditEvent, "before_insert", fail)
        try:
            with pytest.raises(RuntimeError, match="synthetic final audit failure"):
                if operation == "withdraw":
                    withdraw(session, class_id)
                else:
                    grant(session, class_id, reauthorize=True)
        finally:
            event.remove(ClassroomS3AuditEvent, "before_insert", fail)
        session.commit()
        periods = list(session.scalars(select(ClassroomReportingPeriod)))
        assert len(periods) == 1 and periods[0].id == first.id
        assert periods[0].ended_at is None
        assert list(session.scalars(select(ClassroomS3AuditEvent.action))) == ["reporting_granted"]
        if operation == "withdraw":
            assert read(session, class_id).allowed
        else:
            with pytest.raises(classroom.ClassroomDenied):
                read(session, class_id)
