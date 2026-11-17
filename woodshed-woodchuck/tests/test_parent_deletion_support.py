from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.c001_abuse import _identity, record_creation
from app.c001_models import AbuseEvent
from app.child_models import ConsentEvidence
from app.models import WoodchuckProfile, WoodchuckState
from app.parent_deletion_support import ACTION, SupportDenied, SupportTarget, _require_application_secret, fulfill
from app.security import hash_pin, verify_pin


@pytest.fixture
def support_case(play_pg, tmp_path):
    now = datetime.now(timezone.utc)
    role = "play_support_" + uuid4().hex
    changed_columns = {
        # PostgreSQL requires one UPDATE column to take a FOR UPDATE row lock.
        "persistent_team_control": "id",
        "seasons": "id",
        "child_consent_evidence": "id",
        "practice_email_presets": "id",
        "woodchuck_profiles": (
            "woodchuck_id, display_name, instrument, level, goal, first_choice_instrument, game_mode, "
            "display_name_changed_at, level_changed_at, pin_hash, status, deleted_at, retired_woodchuck_id_hash, "
            "session_version, deletion_failed_attempts, deletion_last_failed_at, updated_at"
        ),
        "woodchuck_states": "state_json, revision, updated_at",
        "child_parent_access": "link_hash, link_expires_at, session_hash, session_expires_at",
        "child_director_permissions": "link_hash, link_expires_at, session_hash, session_expires_at",
        "classroom_roster_identities": "school_name",
        "team_memberships": "ended_at",
        "team_join_requests": "status, resolved_at",
        "teams": "creator_profile_id",
        "student_verifier_connections": "status, updated_at",
        "student_organization_memberships": "status",
        "team_reports": "reporter_profile_id",
        "practice_charts": "note, practice_details, ordinary_email_preset_id, ordinary_email_error_code, updated_at",
        "practice_chart_verifications": "response_note, last_email_error_code, status, verifier_id, updated_at",
        "contest_results": "display_name_snapshot",
        "trusted_verifier_invitations": "email, last_email_error_code, status, token_hash, expires_at, updated_at",
        "login_streaks": "current_days, last_login_date, updated_at",
    }
    with play_pg.begin() as session:
        session.execute(text(f"CREATE ROLE {role} LOGIN"))
        for table, columns in changed_columns.items():
            readable = "SELECT (id, profile_id)" if table == "child_consent_evidence" else "SELECT"
            session.execute(text(f"GRANT {readable}, UPDATE ({columns}) ON public.{table} TO {role}"))
        session.execute(text(f"GRANT SELECT ON public.alembic_version, public.c001_abuse_events TO {role}"))
        session.execute(text(f"GRANT DELETE ON public.practice_email_presets, public.analytics_events TO {role}"))
        session.execute(text(f"GRANT SELECT (id, profile_id) ON public.analytics_events TO {role}"))
        session.execute(text(
            "GRANT INSERT (kind, network_key, browser_key, profile_id, created_at, occurrences) "
            f"ON public.c001_abuse_events TO {role}"
        ))
        session.execute(text(f"GRANT USAGE ON SEQUENCE public.c001_abuse_events_id_seq TO {role}"))
        database, oid = session.execute(text(
            "SELECT datname::text, oid::bigint FROM pg_database WHERE datname = current_database()"
        )).one()
        subjects = [
            WoodchuckProfile(woodchuck_id=code, display_name=name, pin_hash=hash_pin("1234"),
                             instrument="Flute", level="Beg", goal="Synthetic goal",
                             created_at=now - timedelta(minutes=20), session_version=4)
            for code, name in [("WC-CASE-ONE", "Synthetic subject"), ("WC-CASE-TWO", "Unrelated subject")]
        ]
        session.add_all(subjects)
        session.flush()
        session.add_all([WoodchuckState(profile_id=profile.id, state_json={"test": True}, revision=3)
                         for profile in subjects])

    def write_case(case):
        path = approvals / (reference + ".json")
        path.write_text(json.dumps(case))
        path.chmod(0o600)

    approved, unrelated = subjects
    approvals = tmp_path / "approvals"
    approvals.mkdir(mode=0o700)
    register = tmp_path / "register"
    register.mkdir(mode=0o700)
    reference = "support-0001"
    lock = approvals / (reference + ".lock")
    lock.touch(mode=0o600)
    case = {
        "case": reference, "status": "approved", "profile_id": approved.id,
        "woodchuck_id": approved.woodchuck_id, "profile_created_at": approved.created_at.isoformat(),
        "requester_ref": "requester-01", "identity_ref": "identity-01", "authority_ref": "authority-01",
        "verified_by": "analyst-0001", "verified_at": (now - timedelta(minutes=10)).isoformat(),
        "disputed": True, "reviewed_by": "reviewer-0002",
        "confirmation": {
            "case": reference, "profile_id": approved.id, "requester_ref": "requester-01",
            "action": ACTION, "reference": "decision-01", "at": (now - timedelta(minutes=5)).isoformat(),
        },
    }
    write_case(case)
    engine = create_engine(play_pg.kw["bind"].url.set(username=role))
    target = SupportTarget(database, oid, role)
    factory = sessionmaker(bind=engine, autoflush=False)

    def run(**changes):
        params = dict(factory=factory, target=target, approval_directory=approvals,
                      approval_uid=os.geteuid(), register=register, reference=reference, clock=lambda: now)
        return fulfill(**(params | changes))

    try:
        yield run, write_case, case, register, unrelated.id, engine
    finally:
        engine.dispose()
        with play_pg.begin() as session:
            session.execute(text(f"DROP OWNED BY {role}"))
            session.execute(text(f"DROP ROLE {role}"))


def test_support_fulfills_without_child_pin_and_repeat_preserves_result(play_pg, support_case):
    run, _, case, register, other_id, _ = support_case
    assert run(status_only=True)["status"] == "no_recorded_execution"
    completed = run()
    assert completed["status"] == "deletion_applied"
    assert run() == run(status_only=True) == completed
    with play_pg() as session:
        child = session.get(WoodchuckProfile, case["profile_id"])
        other = session.get(WoodchuckProfile, other_id)
        assert child.status == "deleted" and child.session_version == 5
        assert not verify_pin("1234", child.pin_hash)
        assert session.get(WoodchuckState, child.id).state_json == {
            "account": {"authenticated": False, "deleted": True}
        }
        assert other.status == "active" and other.session_version == 4
        assert verify_pin("1234", other.pin_hash)
    intent = (register / (case["case"] + ".intent.json")).read_text()
    receipt = (register / (case["case"] + ".result.json")).read_text()
    for private in (case["woodchuck_id"], "requester-01", "identity-01", "authority-01", "decision-01", "deleted$"):
        assert private not in intent and private not in receipt


def test_support_requires_exact_application_secret(monkeypatch):
    original = " synthetic-original-deletion-session-key "
    expected = sha256(original.encode("utf-8")).hexdigest()
    monkeypatch.setenv("SESSION_SECRET", original)
    _require_application_secret(expected)
    with pytest.raises(SupportDenied, match="support_application_session_secret_mismatch"):
        _require_application_secret(None)
    monkeypatch.setenv("SESSION_SECRET", original.strip())
    with pytest.raises(SupportDenied, match="support_application_session_secret_mismatch"):
        _require_application_secret(expected)
    monkeypatch.setenv("SESSION_SECRET", "weak")
    with pytest.raises(SupportDenied, match="support_application_session_secret_required"):
        _require_application_secret(sha256(b"weak").hexdigest())


def test_support_recent_account_preserves_consent_and_records_one_quick_deletion(play_pg, support_case):
    run, _, case, _, _, engine = support_case
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT has_column_privilege('public.child_consent_evidence', 'id', 'UPDATE'), "
            "has_column_privilege('public.child_consent_evidence', 'notice_sha256', 'UPDATE'), "
            "has_column_privilege('public.child_consent_evidence', 'parent_email', 'SELECT')"
        )).one() == (True, False, False)
    profile_id = case["profile_id"]
    network_key = "a" * 64
    identity = _identity.set((network_key, "b" * 64))
    try:
        with play_pg.begin() as session:
            record_creation(session, profile_id)
            verified = datetime.fromisoformat(case["verified_at"])
            consent = ConsentEvidence(profile_id=profile_id, parent_email="guardian@example.invalid",
                                      notice_version="synthetic", notice_sha256="c" * 64,
                                      approved_at=verified, confirmed_at=verified)
            session.add(consent)
            session.flush()
            consent_id = consent.id
    finally:
        _identity.reset(identity)
    result = run()
    assert result["status"] == "deletion_applied"
    assert run() == result
    with play_pg() as session:
        consent = session.get(ConsentEvidence, consent_id)
        assert consent.profile_id == profile_id
        assert consent.notice_sha256 == "c" * 64
        assert consent.parent_email == "guardian@example.invalid"
        assert consent.approved_at == consent.confirmed_at == verified
        assert consent.withdrawn_at is None
        events = session.scalars(select(AbuseEvent).order_by(AbuseEvent.id)).all()
        assert [(event.kind, event.network_key, event.profile_id, event.browser_key, event.occurrences)
                for event in events] == [
            ("c001_creation", network_key, profile_id, "b" * 64, 1),
            ("quick_deletion", network_key, None, None, 1),
        ]


def test_support_refuses_wrong_child_stale_revoked_or_unreviewed_approval(play_pg, support_case):
    run, write_case, case, register, other_id, engine = support_case
    with play_pg() as session:
        oid = session.scalar(text("SELECT oid::bigint FROM pg_database WHERE datname = current_database()"))
    target = SupportTarget(engine.url.database, oid, engine.url.username)
    with pytest.raises(SupportDenied, match="support_target_mismatch"):
        run(target=replace(target, database_oid=target.database_oid + 1))
    with pytest.raises(SupportDenied, match="support_nonadministrative_role_required"):
        run(factory=play_pg, target=replace(target, role=play_pg.kw["bind"].url.username))
    ambiguous = deepcopy(case)
    ambiguous["confirmation"]["profile_id"] = other_id
    wrong_child = deepcopy(case)
    wrong_child["profile_id"] = wrong_child["confirmation"]["profile_id"] = other_id
    cases = (
        (ambiguous, "support_confirmation_mismatch"),
        (wrong_child, "support_child_mismatch_or_unavailable"),
        (case | {"verified_at": "2026-01-01T00:00:00+00:00"}, "support_case_expired"),
        (case | {"confirmation": case["confirmation"] | {"at": case["verified_at"]}},
         "support_case_expired"),
        (case | {"status": "revoked"}, "support_case_invalid"),
        (case | {"reviewed_by": case["verified_by"]}, "support_independent_review_required"),
        (case | {"identity_ref": case["authority_ref"]}, "support_distinct_evidence_required"),
    )
    for invalid, reason in cases:
        write_case(invalid)
        now = datetime.fromisoformat(case["verified_at"]) + timedelta(minutes=32)
        with pytest.raises(SupportDenied, match=reason):
            run(clock=lambda: now)
    with play_pg() as session:
        assert session.get(WoodchuckProfile, case["profile_id"]).status == "active"
        assert session.get(WoodchuckProfile, other_id).status == "active"
    assert not list(register.glob("*.intent.json"))


def test_support_rechecks_confirmation_after_database_work(play_pg, support_case):
    run, _, case, register, _, _ = support_case
    confirmation = datetime.fromisoformat(case["confirmation"]["at"])
    observed = iter((confirmation, confirmation, confirmation + timedelta(minutes=30)))
    with pytest.raises(SupportDenied, match="support_case_expired"):
        run(clock=lambda: next(observed))
    with play_pg() as session:
        child = session.get(WoodchuckProfile, case["profile_id"])
        assert child.status == "active" and child.session_version == 4
    assert list(register.glob("*.intent.json"))
    assert not list(register.glob("*.result.json"))
    with pytest.raises(SupportDenied, match="support_execution_unreconciled"):
        run(status_only=True)


@pytest.mark.parametrize("committed", [False, True])
def test_support_resolves_lost_commit_response_only_from_actual_state(play_pg, support_case, committed):
    run, _, case, register, _, engine = support_case

    class UncertainCommit(Session):
        def commit(self):
            if committed:
                super().commit()
            raise RuntimeError("simulated lost connection")

    with pytest.raises(RuntimeError, match="simulated lost connection"):
        run(factory=sessionmaker(bind=engine, class_=UncertainCommit, autoflush=False))
    assert list(register.glob("*.intent.json"))
    assert not list(register.glob("*.result.json"))
    if committed:
        assert run(status_only=True)["status"] == "deletion_applied"
    else:
        with pytest.raises(SupportDenied, match="support_execution_unreconciled"):
            run()
        with play_pg() as session:
            assert session.get(WoodchuckProfile, case["profile_id"]).status == "active"


def test_support_withholds_receipt_when_tombstone_matches_but_ordinary_data_remains(play_pg, support_case):
    run, _, case, register, _, engine = support_case

    class IntroduceResidualAfterCommit(Session):
        def commit(self):
            super().commit()
            with play_pg.begin() as independent:
                state = independent.get(WoodchuckState, case["profile_id"])
                state.state_json = {"note": "synthetic residual"}

    with pytest.raises(SupportDenied, match="support_ordinary_cleanup_incomplete"):
        run(factory=sessionmaker(bind=engine, class_=IntroduceResidualAfterCommit, autoflush=False))
    assert list(register.glob("*.intent.json"))
    assert not list(register.glob("*.result.json"))
    with play_pg() as session:
        assert session.get(WoodchuckProfile, case["profile_id"]).status == "deleted"
    with pytest.raises(SupportDenied, match="support_ordinary_cleanup_incomplete"):
        run(status_only=True)
