"""Prior ordinary cleanup uses the existing restricted student Support role."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import select, text

from app.account_deletion import DELETED_STATE, anonymize_woodchuck_account, ordinary_deletion_complete
from app.models import (
    AnalyticsEvent, LoginStreak, Organization, PracticeChart, PracticeEmailPreset,
    StudentOrganizationMembership, StudentVerifierConnection, TrustedVerifier, TrustedVerifierInvitation,
    WoodchuckProfile, WoodchuckState,
)
from app.parent_deletion_support import PRIOR_ACTION, SupportDenied, _digest
from tests.test_parent_deletion_support import support_case


@pytest.fixture
def prior_deletion(play_pg, support_case):
    run, publish, original, register, other_id, engine = support_case
    now = datetime.now(timezone.utc)
    deleted_at = now - timedelta(days=52)
    created_at = deleted_at - timedelta(days=31)
    sid = original["profile_id"]
    with play_pg.begin() as session:
        session.get(WoodchuckProfile, sid).created_at = created_at
    original["profile_created_at"] = created_at.isoformat()
    original["verified_at"] = (deleted_at - timedelta(minutes=10)).isoformat()
    original["confirmation"]["at"] = (deleted_at - timedelta(minutes=2)).isoformat()
    publish(original)
    deletion = run(clock=lambda: deleted_at)
    assert deletion["deleted_at"] == deleted_at.isoformat()
    prior_case = {
        "case": "prior-cleanup-001", "status": "approved", "action": PRIOR_ACTION,
        "profile_id": sid, "woodchuck_id": original["woodchuck_id"], "profile_created_at": original["profile_created_at"],
        "deleted_at": deletion["deleted_at"], "deletion_evidence_ref": original["case"], "evidence_kind": "support_receipt",
        "verified_by": "verified-reviewer", "verified_at": (now - timedelta(minutes=10)).isoformat(),
    }
    directory = register.parent / "approvals"
    (directory / (prior_case["case"] + ".lock")).touch(mode=0o600)

    def cleanup(case=None, **kwargs):
        case = prior_case if case is None else case
        path = directory / (prior_case["case"] + ".json")
        path.write_text(json.dumps(case))
        path.chmod(0o600)
        return run(reference=prior_case["case"], prior=True, clock=lambda: now, **kwargs)

    # Model an older deployment's residual rows, using the deletion date and
    # subject from the actual committed deletion above.
    with play_pg.begin() as session:
        profile = session.get(WoodchuckProfile, sid)
        profile.instrument, profile.goal = "Flute", "Sensitive goal"
        state = session.get(WoodchuckState, sid)
        state.state_json = {"old_private_progress": True}
        chart = PracticeChart(profile_id=sid, minutes=45, instrument="Flute",
                              practice_date=created_at.date(), note="Private note",
                              practice_details=["private checklist"], submission_key="stable-score-source")
        historical = TrustedVerifierInvitation(profile_id=sid, email="former@example.test", role="verifier",
                    token_hash="b" * 64, status="expired", expires_at=created_at, updated_at=created_at)
        session.add_all([chart, historical, LoginStreak(profile_id=sid, current_days=3, last_login_date=created_at.date()),
                         PracticeEmailPreset(profile_id=sid, display_name="Private", email="recipient@example.test")])
        for pid in (sid, other_id):
            session.add(AnalyticsEvent(profile_id=pid, event_type="arcade_entered", activity_date=created_at.date()))
    return cleanup, run, prior_case, other_id, register, chart.id, historical.id, engine


def test_older_restriction_gets_its_own_cleanup_receipt_without_resetting_clocks(play_pg, prior_deletion):
    cleanup, run, case, other, register, chart_id, invite_id, engine = prior_deletion
    sid = case["profile_id"]
    original_receipt = (register / (case["deletion_evidence_ref"] + ".result.json")).read_bytes()
    with engine.connect() as connection:
        for relation in ("practice_charts", "weekly_plans", "arcade_high_scores", "account_age_corrections"):
            assert not connection.scalar(text("SELECT has_table_privilege(current_user, :t, 'DELETE')"), {"t": relation})
    def preserved(session):
        profile = session.get(WoodchuckProfile, sid)
        chart = session.get(PracticeChart, chart_id)
        invitation = session.get(TrustedVerifierInvitation, invite_id)
        return ((profile.woodchuck_id, profile.pin_hash, profile.retired_woodchuck_id_hash,
                 profile.deleted_at, profile.session_version, profile.created_at, profile.updated_at),
                (chart.id, chart.practice_date, chart.minutes, chart.instrument, chart.submission_key, chart.updated_at),
                (invitation.id, invitation.status, invitation.expires_at, invitation.updated_at))

    with play_pg() as session:
        before = preserved(session)
        state = session.get(WoodchuckState, sid)
        previous_revision, previous_update = state.revision, state.updated_at
    with pytest.raises(SupportDenied, match="ordinary_cleanup_incomplete"):
        run(status_only=True)
    with pytest.raises(SupportDenied, match="record_mismatch"):
        run(prior=True)  # A previous receipt cannot be relabelled with a new action.
    result = cleanup()
    assert result["status"] == "prior_ordinary_cleanup_verified"
    assert result["deleted_at"] == case["deleted_at"]
    assert datetime.fromisoformat(result["cleanup_at"]) > datetime.fromisoformat(result["deleted_at"]) + timedelta(days=30)
    assert cleanup(status_only=True) == cleanup() == result
    assert (register / (case["deletion_evidence_ref"] + ".result.json")).read_bytes() == original_receipt
    with play_pg() as session:
        assert ordinary_deletion_complete(session, session.get(WoodchuckProfile, sid))
        assert preserved(session) == before
        state = session.get(WoodchuckState, sid)
        assert (state.state_json, state.revision, state.updated_at) == (DELETED_STATE, previous_revision + 1, previous_update)
        assert list(session.scalars(select(AnalyticsEvent.profile_id))) == [other]
        assert list(session.scalars(select(PracticeEmailPreset.id))) == []
        assert session.get(TrustedVerifierInvitation, invite_id).email == "deleted@invalid.local"


@pytest.mark.parametrize("problem", ["date_missing", "date_mismatch", "evidence_missing", "wrong_original_id", "wrong_subject", "corrupt_clock", "missing_receipt"])
def test_historical_cleanup_holds_unproven_subject_or_clock(play_pg, prior_deletion, problem):
    cleanup, _, original, other, register, chart_id, _, _ = prior_deletion
    case = original.copy()
    if problem == "date_missing":
        case.pop("deleted_at")
    elif problem == "date_mismatch":
        case["deleted_at"] = (datetime.fromisoformat(case["deleted_at"]) + timedelta(hours=1)).isoformat()
    elif problem == "evidence_missing":
        case["deletion_evidence_ref"] = None
    elif problem == "wrong_original_id":
        case["woodchuck_id"] = "WC-DIFFERENT"
    elif problem == "wrong_subject":
        case["profile_id"] = other
    elif problem == "missing_receipt":
        case["deletion_evidence_ref"] = "unavailable-record"
    elif problem == "corrupt_clock":
        with play_pg.begin() as session:
            session.get(WoodchuckProfile, case["profile_id"]).deleted_at = None
    with pytest.raises(SupportDenied):
        cleanup(case)
    assert not (register / (case["case"] + ".intent.json")).exists()
    with play_pg() as session:
        assert session.get(PracticeChart, chart_id).note == "Private note"
        assert session.get(WoodchuckProfile, other).status == "active"


def test_external_source_needs_independent_approval_and_matching_retired_id(play_pg, prior_deletion):
    cleanup, _, original, _, _, chart_id, _, _ = prior_deletion
    external = original | {"evidence_kind": "verified_deletion_record", "deletion_evidence_ref": "restricted-source-01"}
    with pytest.raises(SupportDenied):
        cleanup(external | {"verified_at": external["deleted_at"]})
    with pytest.raises(SupportDenied):
        cleanup(external | {"woodchuck_id": "WC-WRONG"})
    with play_pg() as session:
        assert session.get(PracticeChart, chart_id).note == "Private note"
    assert cleanup(external)["status"] == "prior_ordinary_cleanup_verified"


def test_historical_pending_invitation_preserves_its_earlier_expiry(play_pg, prior_deletion):
    clean, _, case, _, _, _, _, _ = prior_deletion
    original_expiry = datetime.fromisoformat(case["deleted_at"]) - timedelta(days=1)
    with play_pg.begin() as session:
        invitation = TrustedVerifierInvitation(
            profile_id=case["profile_id"], role="verifier", status="pending", email="former@example.test",
            created_at=original_expiry - timedelta(days=7), expires_at=original_expiry, token_hash="c" * 64,
        )
        session.add(invitation)
    result = clean()
    with play_pg() as session:
        retained = session.get(TrustedVerifierInvitation, invitation.id)
        assert retained.expires_at == original_expiry
        assert retained.updated_at == datetime.fromisoformat(result["cleanup_at"])
        assert retained.status == "revoked" and retained.token_hash == f"retired:{retained.id}"


def test_student_deletion_preserves_earlier_relationship_end_events(play_pg, prior_deletion):
    cleanup, _, case, fresh_id, _, _, _, _ = prior_deletion
    prior_id = case["profile_id"]
    earlier = datetime.fromisoformat(case["deleted_at"]) - timedelta(days=1)
    now = datetime.now(timezone.utc)
    with play_pg.begin() as session:
        joined = earlier - timedelta(days=3)
        session.get(WoodchuckProfile, fresh_id).created_at = joined
        connections, memberships = [], []
        for sid, status in ((fresh_id, "pending"), (fresh_id, "accepted"), (fresh_id, "disconnected"),
                            (fresh_id, "legacy_ended"), (prior_id, "disconnected")):
            adult = TrustedVerifier(email=f"{status}-{sid}@example.test", display_name="Verifier",
                                    pin_hash="synthetic", created_at=joined)
            session.add(adult)
            session.flush()
            row = StudentVerifierConnection(profile_id=sid, verifier_id=adult.id, role="verifier", status=status,
                invited_at=earlier - timedelta(days=2), accepted_at=None if status == "pending" else earlier,
                updated_at=earlier)
            session.add(row)
            connections.append(row)
        for sid, status in ((fresh_id, "active"), (fresh_id, "inactive"), (fresh_id, "legacy_ended"), (prior_id, "inactive")):
            org = Organization(name=status, organization_type="school", created_at=joined)
            session.add(org)
            session.flush()
            row = StudentOrganizationMembership(profile_id=sid, organization_id=org.id, status=status, joined_at=earlier)
            session.add(row)
            memberships.append(row)
    with play_pg.begin() as session:
        anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, fresh_id), now=now)
    assert cleanup()["status"] == "prior_ordinary_cleanup_verified"
    with play_pg() as session:
        for old in connections:
            stored = session.get(StudentVerifierConnection, old.id)
            revoked = old.profile_id == fresh_id and old.status in ("pending", "accepted")
            assert (stored.status, stored.updated_at) == (("disconnected", now) if revoked else (old.status, earlier))
            assert (stored.invited_at, stored.accepted_at) == (old.invited_at, old.accepted_at)
        for old in memberships:
            stored = session.get(StudentOrganizationMembership, old.id)
            assert stored.status == ("inactive" if old.status == "active" else old.status)
            assert stored.joined_at == old.joined_at


@pytest.mark.parametrize("problem", ["receipt_digest", "different_executor"])
def test_prior_cleanup_refuses_contradictory_original_receipt(play_pg, prior_deletion, problem):
    cleanup, _, case, _, register, chart_id, _, _ = prior_deletion
    receipt = register / (case["deletion_evidence_ref"] + ".result.json")
    contents = json.loads(receipt.read_text())
    if problem == "different_executor":
        original = register / (case["deletion_evidence_ref"] + ".intent.json")
        intent = json.loads(original.read_text())
        intent["role"] = "different_support_executor"
        original.write_text(json.dumps(intent))
        contents["intent_sha256"] = _digest(intent)
    else:
        contents["intent_sha256"] = "f" * 64
    receipt.write_text(json.dumps(contents))
    with pytest.raises(SupportDenied, match="prior_receipt_mismatch"):
        cleanup(case | {"evidence_kind": "verified_deletion_record"})
    with play_pg() as session:
        assert session.get(PracticeChart, chart_id).note == "Private note"
    assert not (register / (case["case"] + ".intent.json")).exists()
