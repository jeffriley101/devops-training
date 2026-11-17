"""Qualify the actual Support operation with and without narrowly scoped local database rights."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import account_routes
from app.main import app
from app.age_models import AccountPrivacy
from app.age_privacy import declare_age
from app.models import ArcadeHighScore, PracticeChart, TrustedVerifierInvitation
from app.parent_deletion_support import SupportDenied
from app.security import hash_invitation_token
from tests.test_historical_deletion_cleanup import prior_deletion
from tests.test_parent_deletion_support import support_case


def qualify_student_disposal(play_pg, engine):
    # These database objects and roles exist only in the fixture's private cluster.
    read = {
        "arcade_start_requests": "profile_id, play_id",
        "arcade_play_sessions": "id, profile_id, pack_id",
        "arcade_attempt_packs": "id, profile_id",
        "weekly_wheel_plays": "id, profile_id",
        "reward_inventory_placements": "profile_id, crown_award_id, reward_grant_id",
        "crown_awards": "id, profile_id", "reward_grants": "id, profile_id, source_key",
        "camp_point_awards": "profile_id, duplicate_key",
        "classroom_chart_shares": "chart_id", "classroom_chart_director_shares": "chart_id",
        "classroom_reporting_periods": "profile_id, starts_at, ended_at",
        "classroom_progress_authorizations": "profile_id, accepted_at, withdrawn_at",
        "classroom_guardian_authorizations": "profile_id, purpose, accepted_at, expires_at, withdrawn_at",
        "weekly_plans": "profile_id, issued_at",
        "account_privacy_rules": "profile_id, consent_id, private_plunge_best",
    }
    private_tables = (
        "arcade_high_scores", "plunge_point_awards", "daily_trivia_attempts", "quest_completions",
        "weekly_newsletter_presentations", "owned_item_copies", "traveling_cup_placements", "login_streaks",
    )
    for table in (*private_tables, "account_age_corrections", "child_parent_age_declarations",
                  "classroom_student_memberships", "classroom_student_invitations", "classroom_enrollment_invitations",
                  "team_week_membership_snapshots"):
        read[table] = "profile_id"
    deletable = (*private_tables, "arcade_start_requests", "arcade_play_sessions", "arcade_attempt_packs",
                 "weekly_wheel_plays", "reward_inventory_placements", "practice_charts", "weekly_plans",
                 "account_privacy_rules")
    writable = {
        "woodchuck_profiles": "plunge_best_score", "account_privacy_rules": "private_plunge_best",
        "practice_charts": "ordinary_email_attempted_at, ordinary_email_sent_at",
        "practice_chart_verifications": "last_email_attempt_at, last_email_sent_at, email_attempt_count",
        "trusted_verifier_invitations": "last_email_attempt_at, last_email_sent_at, email_attempt_count",
    }
    role = engine.url.username
    with play_pg.begin() as session:
        for table, columns in read.items():
            session.execute(text(f"GRANT SELECT ({columns}) ON {table} TO {role}"))
        for table, columns in writable.items():
            session.execute(text(f"GRANT UPDATE ({columns}) ON {table} TO {role}"))
        session.execute(text(f"GRANT DELETE ON {', '.join(deletable)} TO {role}"))


def private_rows(session, sid, tag):
    now = datetime.now(timezone.utc)
    score = ArcadeHighScore(profile_id=sid, game_key="thirds", best_score=47)
    chart = PracticeChart(profile_id=sid, practice_date=now.date(), minutes=10, instrument="Flute", source="p-book",
                          include_contests=False, include_team_contests=False, credits_awarded=0)
    invitation = TrustedVerifierInvitation(profile_id=sid, email=tag + "@example.test", role="verifier",
        status="expired", token_hash=hash_invitation_token(tag), expires_at=now-timedelta(days=2),
        last_email_sent_at=now-timedelta(days=4), email_attempt_count=2)
    rule = declare_age(session, sid, "13to17")
    session.add_all((score, chart, invitation))
    session.flush()
    return score.id, chart.id, invitation.id, rule.profile_id


def test_verified_guardian_disposes_private_rows_with_fresh_readback(play_pg, support_case):
    run, _, case, _, other_id, engine = support_case
    sid = case["profile_id"]
    qualify_student_disposal(play_pg, engine)
    with play_pg.begin() as session:
        score, chart, invitation, _ = private_rows(session, sid, "subject")
        other_score, other_chart, other_invite, _ = private_rows(session, other_id, "neighbor")
    result = run()
    assert result["disposal"] == dict.fromkeys(
        ("private_activity", "practice_and_plans", "age_history", "delivery_history"), "verified")
    assert run(status_only=True) == result
    with play_pg.begin() as session:
        assert session.get(ArcadeHighScore, score) is None and session.get(PracticeChart, chart) is None
        assert session.get(AccountPrivacy, sid) is None
        assert session.get(TrustedVerifierInvitation, invitation).last_email_sent_at is None
        assert session.get(ArcadeHighScore, other_score).best_score == 47
        assert session.get(PracticeChart, other_chart).minutes == 10
        assert session.get(AccountPrivacy, other_id).age_band == "13to17"
        assert session.get(TrustedVerifierInvitation, other_invite).email_attempt_count == 2
        session.add(ArcadeHighScore(profile_id=sid, game_key="thirds", best_score=47))
    with pytest.raises(SupportDenied, match="associated_cleanup_unverified"):
        run(status_only=True)


def test_prior_cleanup_keeps_original_receipt_and_observes_actual_role(play_pg, prior_deletion):
    clean, _, case, other_id, register, _, _, engine = prior_deletion
    original = register / (case["deletion_evidence_ref"] + ".result.json")
    original_bytes = original.read_bytes()
    with play_pg.begin() as session:
        score = ArcadeHighScore(profile_id=case["profile_id"], game_key="thirds", best_score=23)
        session.add(score)
    qualify_student_disposal(play_pg, engine)
    result = clean()
    assert set(result["disposal"].values()) == {"verified"}
    assert result["deleted_at"] == case["deleted_at"] and result["cleanup_at"] != result["deleted_at"]
    assert clean(status_only=True) == result and original.read_bytes() == original_bytes
    with play_pg() as session:
        assert session.get(ArcadeHighScore, score.id) is None


def test_unqualified_role_cannot_claim_extended_disposal(play_pg, support_case):
    run, _, case, _, _, _ = support_case
    with play_pg.begin() as session:
        score, chart, invitation, _ = private_rows(session, case["profile_id"], "restricted")
    result = run()
    assert result["status"] == "deletion_applied"
    assert set(result["disposal"].values()) == {"access_required"}
    assert run(status_only=True) == result
    with play_pg() as session:
        assert session.get(ArcadeHighScore, score) and session.get(PracticeChart, chart)
        assert session.get(AccountPrivacy, case["profile_id"])
        assert session.get(TrustedVerifierInvitation, invitation).last_email_sent_at is not None


def test_student_sees_pending_cleanup_when_deletion_role_is_limited(play_pg, support_case, monkeypatch):
    _, _, case, _, _, engine = support_case
    with play_pg.begin() as session:
        score, _, _, _ = private_rows(session, case["profile_id"], "restricted-web")
    credentials = {"woodchuck_id": case["woodchuck_id"], "pin": "1234"}
    with TestClient(app) as client:
        assert client.post("/account/login", data=credentials).status_code == 200
        monkeypatch.setattr(account_routes, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
        response = client.post("/account/delete", data={**credentials, "confirmation": "DELETE"})
        assert response.status_code == 200 and "Some private data still needs an authorized cleanup" in response.text
        assert client.get("/account/me").json()["authenticated"] is False
    with play_pg() as session:
        assert session.get(ArcadeHighScore, score) is not None


def test_hidden_chart_evidence_holds_charts_without_blocking_other_cleanup(play_pg, support_case):
    run, _, case, _, _, engine = support_case
    qualify_student_disposal(play_pg, engine)
    with play_pg.begin() as session:
        score, chart, invitation, _ = private_rows(session, case["profile_id"], "filtered")
        session.execute(text("ALTER TABLE classroom_chart_shares ENABLE ROW LEVEL SECURITY"))
    result = run()
    assert result["disposal"] == {
        "private_activity": "verified", "practice_and_plans": "access_required",
        "age_history": "verified", "delivery_history": "verified",
    }
    assert run(status_only=True) == result
    with play_pg() as session:
        assert session.get(PracticeChart, chart) and session.get(AccountPrivacy, case["profile_id"])
        assert session.get(ArcadeHighScore, score) is None
        assert session.get(TrustedVerifierInvitation, invitation).last_email_sent_at is None
