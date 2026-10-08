"""Synthetic feature-source policy tests; no game implementation or reporting grant."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import arcade_access, classroom_s2 as s2, classroom_capabilities as capabilities
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence
from app.models import (
    Membership, PracticeChartVerification, StudentVerifierConnection,
    TesterEnrollment as Enrollment, WoodchuckProfile,
)
from app.memberships import Actor, create_complimentary_membership, student_has_full_access
from app.feature_access import can_use_feature
from app.arcade_rewards import ARCADE_PLAY_GAME_KEYS, validate_arcade_play_game_key
from app.classroom_models import ClassroomEntitlement, ClassroomMembershipPeriod
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import NOW, code, create, join, ready, s2_db, student


@pytest.fixture
def capability_db(s2_db, monkeypatch):
    monkeypatch.setenv("CLASSROOM_S3_ENABLED", "1")
    return s2_db


def personal(session):
    return create_complimentary_membership(session, Actor("student", 1), Actor("admin"),
                                           at=NOW - timedelta(days=1))


def add_tester(session):
    row = Enrollment(profile_id=1, cohort_key="C001", joined_at=NOW - timedelta(days=1))
    session.add(row)
    session.flush()
    return row


def decide(session, key, **scope):
    return capabilities.capability_decision(session, 1, key, **scope)


@pytest.mark.parametrize("source", ["ordinary", "personal", "tester", "classroom", "personal_classroom"])
def test_source_matrix_preserves_only_express_grants(capability_db, source):
    with capability_db() as session:
        if source in {"personal", "personal_classroom"}:
            personal(session)
        if source == "tester":
            add_tester(session)
        if source in {"classroom", "personal_classroom"}:
            ready(session)
            join(session)
        full = source in {"personal", "tester", "personal_classroom"}
        classroom = source in {"classroom", "personal_classroom"}
        for key in capabilities.ORDINARY_CAPABILITIES:
            assert decide(session, key).allowed
        assert decide(session, "full_access").allowed is full
        assert student_has_full_access(session, 1, at=NOW) is full
        assert decide(session, "normal_arcade_waiver").allowed is full
        assert decide(session, "practice_insights").allowed is full
        assert can_use_feature(session, 1, "practice_insights") is full
        for key in arcade_access.CLASSROOM:
            assert decide(session, key).allowed is classroom
            policy = arcade_access.access_policy(session, 1, key)
            assert policy["allowed"] is classroom
            assert policy["free_reason"] == ("classroom" if classroom else None)
        for key in ("class_reporting", "export", "premium_appearance", "verification", "xp", "contest_credit"):
            assert not decide(session, key).allowed
            assert decide(session, key).reason == "capability_not_approved"
        assert not s2.entitlement_decision(session, 2).allowed


def test_exact_source_ids_and_independent_class_contributions(capability_db, monkeypatch):
    with capability_db() as session:
        first, _, _ = ready(session)
        member1 = join(session)
        second = create(session, name="Second Class")
        code(session, second.id, preferred="SECOND88")
        member2 = join(session, "SECOND88")
        initial = decide(session, "note-names")
        grants = [row for row in initial.sources if row.allowed]
        assert {row.class_id for row in grants} == {first.id, second.id}
        assert {row.membership_id for row in grants} == {member1.id, member2.id}
        assert all(row.program_id == 1 and row.entitlement_id and row.membership_period_id for row in grants)
        monkeypatch.setattr(s2, "_now", lambda: NOW + timedelta(minutes=1))
        s2.suspend_member(session, actor=actor(session), program_id=1, class_id=first.id,
                          membership_id=member1.id, reason="conduct")
        result = decide(session, "note-names")
        assert result.allowed
        assert [(row.class_id, row.allowed, row.reason) for row in result.sources
                if row.source_type == "classroom"] == [
            (first.id, False, "membership_held"), (second.id, True, "active_classroom_source")]
        assert not decide(session, "note-names", program_id=1, class_id=first.id).allowed
        assert not decide(session, "note-names", program_id=2, class_id=second.id).allowed
        assert not decide(session, "note-names", program_id=2).allowed
        assert not decide(session, "note-names", class_id=second.id).allowed


def test_expired_program_cannot_rescue_another_program(capability_db):
    with capability_db() as session:
        first, _, _ = ready(session)
        join(session)
        session.commit()
        second, _, _ = ready(session, program_id=2, preferred="PROGRAM2")
        join(session, "PROGRAM2", program_id=2)
        entitlement = session.scalar(select(ClassroomEntitlement).where(ClassroomEntitlement.program_id == 1))
        entitlement.starts_at, entitlement.ends_at = NOW - timedelta(days=2), NOW - timedelta(days=1)
        session.flush()
        assert decide(session, "note-names").allowed
        assert not decide(session, "note-names", program_id=1, class_id=first.id).allowed
        assert decide(session, "note-names", program_id=2, class_id=second.id).allowed


@pytest.mark.parametrize("ending", ["leave", "archive", "expiry"])
def test_classroom_loss_keeps_ordinary_and_personal_sources(capability_db, monkeypatch, ending):
    with capability_db() as session:
        full = personal(session)
        klass, _, _ = ready(session)
        member = join(session)
        monkeypatch.setattr(s2, "_now", lambda: NOW + timedelta(minutes=1))
        if ending == "leave":
            s2.leave_class(session, student=student(session), program_id=1, class_id=klass.id)
        elif ending == "archive":
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=klass.id, state="archived")
        else:
            monkeypatch.setattr(s2, "_now", lambda: NOW + timedelta(days=15))
        assert not decide(session, "note-names").allowed
        for key in capabilities.FULL_CAPABILITIES:
            assert decide(session, key).allowed
        assert session.get(Membership, full.id).status == "active"
        assert session.scalar(select(ClassroomMembershipPeriod.id).where(
            ClassroomMembershipPeriod.membership_id == member.id))


@pytest.mark.parametrize("mode", ["inactive", "unknown", "under13", "withdrawn_child"])
def test_underlying_account_authority_blocks_every_source(capability_db, monkeypatch, mode):
    with capability_db() as session:
        personal(session)
        add_tester(session)
        ready(session)
        join(session)
        profile = session.get(WoodchuckProfile, 1)
        rule = session.get(AccountPrivacy, 1)
        if mode == "inactive":
            profile.status = "deleted"
        elif mode == "unknown":
            rule.age_band = "unknown"
        else:
            rule.age_band = "under13"
            if mode == "withdrawn_child":
                from app import child_authorization
                monkeypatch.setattr(child_authorization, "under13_available", lambda: True)
                monkeypatch.setattr(child_authorization, "notice_policy", lambda: ("synthetic", "", "a" * 64))
                consent = ConsentEvidence(profile_id=1, parent_email="parent@example.test",
                    approved_at=NOW, confirmed_at=NOW, notice_version="synthetic", notice_sha256="a" * 64,
                    withdrawn_at=NOW + timedelta(seconds=1))
                session.add(consent)
                session.flush()
                rule.consent_id = consent.id
        session.flush()
        result = capabilities.resolve_student_capabilities(session, 1)
        assert all(not decision.allowed for decision in result.values())
        assert all(decision.sources[0].reason == "account_ineligible" for decision in result.values())


def test_disabled_classroom_and_guest_fail_closed_preserving_ordinary(capability_db, monkeypatch):
    with capability_db() as session:
        ready(session)
        join(session)
        monkeypatch.delenv("CLASSROOM_S3_ENABLED")
        assert not decide(session, "note-names").allowed
        assert decide(session, "pristine_use").allowed
        assert decide(session, "personal_progress_save").allowed
        for profile_id in (None, 0, True, 9999):
            assert not capabilities.capability_decision(session, profile_id, "note-names").allowed


def test_full_evidence_retains_personal_and_tester_ids_without_program_grant(capability_db):
    with capability_db() as session:
        membership, enrollment = personal(session), add_tester(session)
        sources = [source for source in decide(session, "full_access").sources if source.allowed]
        assert {(source.source_type, source.source_id) for source in sources} == {
            ("personal_full", membership.id), ("tester_lifetime", enrollment.id)}
        assert all(source.program_id is None and source.entitlement_id is None for source in sources)
        assert not decide(session, "note-names").allowed
        assert not s2.entitlement_decision(session, 1).allowed


def test_existing_child_account_consent_allows_tools_without_reporting(capability_db, monkeypatch):
    from app import child_authorization
    monkeypatch.setattr(child_authorization, "under13_available", lambda: True)
    monkeypatch.setattr(child_authorization, "notice_policy", lambda: ("synthetic", "", "a" * 64))
    with capability_db() as session:
        ready(session)
        join(session)
        consent = ConsentEvidence(profile_id=1, parent_email="parent@example.test", approved_at=NOW,
            confirmed_at=NOW, notice_version="synthetic", notice_sha256="a" * 64)
        session.add(consent)
        session.flush()
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.consent_id = "under13", consent.id
        session.flush()
        assert decide(session, "note-names").allowed
        assert decide(session, "pristine_use").allowed
        assert decide(session, "personal_progress_save").allowed
        assert not decide(session, "class_reporting").allowed
        assert not decide(session, "full_access").allowed


def test_arcade_playable_normal_and_always_free_policies_unchanged(capability_db):
    with capability_db() as session:
        ready(session)
        join(session)
        assert arcade_access.CLASSROOM == {
            "note-names": "Note Names", "instrument-fingerings": "Instrument Fingerings",
            "rhythm-hear-pick": "Rhythm — Hear & Pick", "key-signatures": "Key Signatures",
            "transposition": "Transposition",
        }
        assert ARCADE_PLAY_GAME_KEYS == arcade_access.ALWAYS_FREE | arcade_access.NORMAL
        assert arcade_access.ALWAYS_FREE == {"plunge-burrow", "blue"}
        for key in arcade_access.CLASSROOM:
            with pytest.raises(ValueError):
                validate_arcade_play_game_key(key)
        for key in arcade_access.NORMAL:
            policy = arcade_access.access_policy(session, 1, key)
            assert policy["allowed"] and policy["free_reason"] is None
            assert policy["entry_cost"] == 100
        for key in arcade_access.ALWAYS_FREE:
            policy = arcade_access.access_policy(session, 1, key)
            assert policy["allowed"] and policy["free_reason"] == "always_free" and policy["entry_cost"] == 0


def test_pristine_saving_without_reporting_retains_existing_self_report_rules(capability_db):
    from app.practice_charts import create_pristine_practice_chart
    with capability_db() as session:
        ready(session)
        join(session)
        assert not decide(session, "class_reporting").allowed
        result = create_pristine_practice_chart(session, profile=session.get(WoodchuckProfile, 1),
            detected_playing_seconds=65, submission_key="s3-synthetic-pristine",
            practice_date=datetime.now(timezone.utc).astimezone(ZoneInfo("America/Chicago")).date())
        assert result.created and result.verification is None
        assert result.chart.source == "pristine" and result.chart.credits_awarded == 0
        assert not result.chart.include_contests and not result.chart.include_team_contests
        assert session.scalar(select(PracticeChartVerification.id)) is None
        assert session.scalar(select(StudentVerifierConnection.id)) is None
