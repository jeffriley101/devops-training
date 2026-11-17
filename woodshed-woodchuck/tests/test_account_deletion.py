from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests.team_factory import make_team

from app import account_routes, age_routes, main as main_module, session_revocations, verifier_routes
from app.account_deletion import DELETED_PUBLIC_NAME, anonymize_woodchuck_account
from app.accounts import create_woodchuck_profile, normalize_woodchuck_id, retired_identifier_hash
from app.account_routes import SESSION_PROFILE_ID, SESSION_PROFILE_VERSION
from app.age_privacy import declare_age
from app.db import Base
from app.main import app
from app.models import (
    CampPointAward, Contest, ContestResult, ContestWeek, CrownProgress,
    PracticeChart, PracticeChartVerification, PracticeEmailPreset, RewardGrant,
    Season, StudentVerifierConnection, Team, TeamFamily, TeamMembership,
    TrustedVerifier, TrustedVerifierInvitation, WoodchuckProfile, WoodchuckState,
)
from app.security import hash_invitation_token, hash_pin
from app.verifiers import accept_trusted_verifier_invitation


NOW = datetime(2026, 8, 3, 20, tzinfo=timezone.utc)


def utc_time(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


@pytest.fixture(params=["sqlite", "postgresql"] if os.getenv("WW_PLAY_DELETION_PG") == "1" else ["sqlite"])
def deletion_db(request, monkeypatch: pytest.MonkeyPatch):
    if request.param == "postgresql":
        return request.getfixturevalue("play_pg")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(age_routes, "SessionLocal", factory)
    monkeypatch.setattr(main_module, "SessionLocal", factory)
    monkeypatch.setattr(verifier_routes, "SessionLocal", factory)
    monkeypatch.setattr(session_revocations, "SessionLocal", factory)
    return factory


def profile(session: Session, number: int, *, name: str | None = None) -> WoodchuckProfile:
    row = WoodchuckProfile(
        woodchuck_id=f"WC-DELETE-{number}",
        display_name=name or f"Delete Student {number}", pin_hash=hash_pin("1234"),
        instrument="Flute", level="Beginner", goal="Practice",
    )
    session.add(row); session.flush()
    session.add(WoodchuckState(
        profile_id=row.id,
        state_json={"account": {"woodchuckId": row.woodchuck_id}, "progress": {"credits": 99}},
        revision=1,
    ))
    return row


def login_cookie(client: TestClient, profile_id: int, version: int = 0) -> None:
    with client as active:
        active.cookies.clear()
        active.app  # keep mypy/runtime happy
        with active.session_transaction() if hasattr(active, "session_transaction") else _noop():
            pass


class _noop:
    def __enter__(self): return self
    def __exit__(self, *_args): return False


def seed_history(session: Session):
    deleted = profile(session, 1)
    teammate = profile(session, 2)
    other = profile(session, 3)
    season = Season(
        key="band-camp-delete", name="Band Camp Delete", timezone="America/Chicago",
        starts_on=date(2026, 7, 27), status="active",
    )
    session.add(season); session.flush()
    team = make_team(session,
        season_id=season.id, display_name="Keep Team", normalized_name="keep team",
        emblem_key="emoji:goat", creator_profile_id=deleted.id,
    )
    session.add(team); session.flush()
    session.add_all([
        TeamMembership(
            season_id=season.id, team_id=team.id, profile_id=deleted.id,
            selected_week_start=date(2026, 7, 27), started_at=NOW - timedelta(days=5),
        ),
        TeamMembership(
            season_id=season.id, team_id=team.id, profile_id=teammate.id,
            selected_week_start=date(2026, 7, 27), started_at=NOW - timedelta(days=5),
        ),
    ])
    preset = PracticeEmailPreset(
        profile_id=deleted.id, display_name="Parent", email="parent@example.com"
    )
    session.add(preset); session.flush()
    chart = PracticeChart(
        profile_id=deleted.id, practice_date=date(2026, 7, 30), minutes=45,
        instrument="Flute", note="private note", practice_details=["private detail"],
        source="p-book", credits_awarded=0, include_contests=True,
        include_team_contests=True, team_id=team.id,
        ordinary_email_preset_id=preset.id,
    )
    session.add(chart); session.flush()
    verifier = TrustedVerifier(
        email="verifier@example.com", display_name="Verifier", pin_hash=hash_pin("2468")
    )
    session.add(verifier); session.flush()
    session.add_all([
        StudentVerifierConnection(
            profile_id=deleted.id, verifier_id=verifier.id, role="parent",
            status="accepted", accepted_at=NOW - timedelta(days=2),
        ),
        StudentVerifierConnection(
            profile_id=other.id, verifier_id=verifier.id, role="mentor",
            status="accepted", accepted_at=NOW - timedelta(days=2),
        ),
    ])
    verification = PracticeChartVerification(
        practice_chart_id=chart.id, verifier_id=verifier.id, status="pending",
        response_note="private response",
    )
    invitation = TrustedVerifierInvitation(
        profile_id=deleted.id, email="pending@example.com", role="parent",
        token_hash=hash_invitation_token("pending-token"), status="pending",
        expires_at=NOW + timedelta(days=1),
    )
    session.add_all([verification, invitation]); session.flush()
    week = ContestWeek(
        season_id=season.id, week_start=date(2026, 7, 27), week_end=date(2026, 8, 3),
        verification_deadline_at=NOW - timedelta(hours=2),
        finalize_after=NOW - timedelta(hours=1), status="finalized", finalized_at=NOW,
    )
    contest = Contest(
        key="delete-test", name="Delete Test", metric_type="points",
        subject_type="student", active=True,
    )
    session.add_all([week, contest]); session.flush()
    result = ContestResult(
        contest_week_id=week.id, contest_id=contest.id, division="open",
        subject_type="student", subject_key=str(deleted.id), profile_id=deleted.id,
        instrument="Flute", display_name_snapshot=deleted.display_name,
        score=45, rank=1, medal="gold",
    )
    session.add(result); session.flush()
    session.add_all([
        RewardGrant(
            profile_id=deleted.id, contest_result_id=result.id, source_key="delete:reward",
            reward_type="dandelion", amount=50,
        ),
        CampPointAward(
            profile_id=deleted.id, activity_type="contest-placement", points_awarded=3,
            occurred_at=NOW, duplicate_key="delete:camp", team_id=team.id,
        ),
        CrownProgress(profile_id=deleted.id, category_key="delete-crown", qualifying_wins=1),
    ])
    session.commit()
    return deleted, teammate, other, team, chart, verifier, invitation, result


def test_anonymization_preserves_history_and_removes_private_data(deletion_db) -> None:
    with deletion_db() as session:
        deleted, teammate, other, team, chart, verifier, invitation, result = seed_history(session)
        other_balance = session.get(WoodchuckState, other.id).state_json
        original = {
            "minutes": chart.minutes, "date": chart.practice_date,
            "team_id": chart.team_id, "score": result.score,
            "rank": result.rank, "medal": result.medal,
            "family_id": team.family_id,
        }
        anonymize_woodchuck_account(session, profile=deleted, now=NOW)
        session.commit(); session.expire_all()

        deleted = session.get(WoodchuckProfile, deleted.id)
        assert deleted.status == "deleted"
        assert utc_time(deleted.deleted_at) == NOW
        assert deleted.display_name == DELETED_PUBLIC_NAME
        assert deleted.woodchuck_id.startswith("WD-") and deleted.session_version == 1
        chart = session.get(PracticeChart, chart.id)
        assert (chart.minutes, chart.practice_date, chart.team_id) == (
            original["minutes"], original["date"], original["team_id"]
        )
        assert chart.note is None and chart.practice_details == []
        result = session.get(ContestResult, result.id)
        assert (result.score, result.rank, result.medal) == (
            original["score"], original["rank"], original["medal"]
        )
        assert result.display_name_snapshot == DELETED_PUBLIC_NAME
        assert session.get(Team, team.id).creator_profile_id is None
        assert session.get(Team, team.id).family_id == original["family_id"]
        assert session.get(TeamFamily, original["family_id"]) is not None
        assert utc_time(session.scalar(select(TeamMembership).where(
            TeamMembership.profile_id == deleted.id
        )).ended_at) == NOW
        assert session.scalar(select(TeamMembership).where(
            TeamMembership.profile_id == teammate.id
        )).ended_at is None
        assert session.scalar(select(func.count()).select_from(PracticeEmailPreset).where(
            PracticeEmailPreset.profile_id == deleted.id
        )) == 0
        assert session.get(TrustedVerifierInvitation, invitation.id).status == "revoked"
        assert session.get(TrustedVerifierInvitation, invitation.id).email == "deleted@invalid.local"
        assert session.scalar(select(PracticeChartVerification).where(
            PracticeChartVerification.practice_chart_id == chart.id
        )).status == "revoked"
        assert session.scalar(select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id == deleted.id
        )).status == "disconnected"
        assert session.scalar(select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id == other.id,
            StudentVerifierConnection.verifier_id == verifier.id,
        )).status == "accepted"
        assert session.scalar(select(func.count()).select_from(RewardGrant).where(
            RewardGrant.profile_id == deleted.id
        )) == 1
        assert session.scalar(select(func.count()).select_from(CampPointAward).where(
            CampPointAward.profile_id == deleted.id
        )) == 1
        assert session.scalar(select(func.count()).select_from(CrownProgress).where(
            CrownProgress.profile_id == deleted.id
        )) == 1
        assert session.get(WoodchuckState, other.id).state_json == other_balance


def test_signed_out_privacy_policy_offers_email_deletion_request(deletion_db) -> None:
    response = TestClient(app).get("/privacy#account-deletion", follow_redirects=False)
    assert response.status_code == 200
    assert 'href="#account-deletion"' in response.text
    heading = '<h2 id="account-deletion">Request Account Deletion</h2>'
    assert response.text.index(heading) < response.text.index("Information we collect")
    request_section = response.text.split(heading, 1)[1].split("</section>", 1)[0]
    request_link = 'href="mailto:support@woodshedwoodchuck.com?subject=Account%20Deletion%20Request"'
    assert request_link in request_section
    assert "without signing in or reinstalling the app" in request_section
    assert "A parent or legal guardian" in request_section
    assert "The child's PIN is unnecessary." in request_section
    assert 'href="#retention-and-deletion"' in request_section
    assert 'id="retention-and-deletion"' in response.text


def test_under13_student_can_read_public_policy_and_delete_without_parent_permission(deletion_db) -> None:
    with deletion_db() as session:
        student = profile(session, 9)
        woodchuck_id = student.woodchuck_id
        declare_age(session, student.id, "under13")
        session.commit()

    client = TestClient(app)
    credentials = {"woodchuck_id": woodchuck_id, "pin": "1234"}
    login = client.post("/account/login", data=credentials)
    assert login.status_code == 200 and login.json()["age_screen_required"] is True
    assert 'href="/account/privacy"' in client.get("/account/age").text
    assert client.get("/account/privacy").status_code == 200

    policy = client.get(
        "/privacy#account-deletion", headers={"Accept": "text/html"}, follow_redirects=False,
    )
    assert policy.status_code == 200
    assert policy.headers["Cache-Control"] == "no-store"
    assert 'id="account-deletion"' in policy.text
    assert woodchuck_id not in policy.text
    assert 'id="account-state-bootstrap" type="application/json">null</script>' in policy.text

    response = client.post(
        "/account/delete", data={**credentials, "confirmation": "DELETE"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/account/me").json()["authenticated"] is False


def test_delete_route_requires_exact_credentials_and_revokes_cookie(deletion_db) -> None:
    with deletion_db() as session:
        deleted = profile(session, 10)
        old_id = deleted.woodchuck_id
        session.commit()
        profile_id = deleted.id
    client = TestClient(app)
    receipt_text = "Your Woodchuck sign-in is disabled. Your saved preferences and practice notes have been removed."
    assert receipt_text not in client.get("/?account_deleted=1").text
    assert client.get("/account/delete").status_code == 405
    assert client.post("/account/delete", data={
        "woodchuck_id": old_id, "pin": "1234", "confirmation": "DELETE",
    }).status_code == 401
    login = client.post("/account/login", data={"woodchuck_id": old_id, "pin": "1234"})
    assert login.status_code == 200
    privacy = client.get("/account/privacy")
    assert privacy.status_code == 200
    assert "Delete Your Woodchuck Account" in privacy.text
    assert 'href="/privacy#account-deletion"' in privacy.text
    assert 'href="/privacy#retention-and-deletion"' in privacy.text
    assert "Some records may remain linked" in privacy.text
    assert 'name="pin"' in privacy.text and 'name="pin" value=' not in privacy.text
    second = TestClient(app)
    assert second.post("/account/login", data={"woodchuck_id": old_id, "pin": "1234"}).status_code == 200
    for bad in (
        {"woodchuck_id": "WC-WRONG", "pin": "1234", "confirmation": "DELETE"},
        {"woodchuck_id": old_id, "pin": "9999", "confirmation": "DELETE"},
        {"woodchuck_id": old_id, "pin": "1234", "confirmation": "delete"},
    ):
        assert client.post("/account/delete", data=bad).status_code == 400
    response = client.post("/account/delete", data={
        "woodchuck_id": old_id, "pin": "1234", "confirmation": "DELETE",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert receipt_text in response.text
    assert receipt_text not in client.get("/").text
    assert client.get("/account/me").json()["authenticated"] is False
    assert second.get("/account/me").json()["authenticated"] is False
    assert client.post("/account/login", data={
        "woodchuck_id": old_id, "pin": "1234"
    }).status_code == 401
    with deletion_db() as session:
        row = session.get(WoodchuckProfile, profile_id)
        assert row.status == "deleted" and row.retired_woodchuck_id_hash


def test_confirmation_failures_are_counted_and_rate_limited(deletion_db) -> None:
    with deletion_db() as session:
        student = profile(session, 12)
        session.commit()
        login_id = student.woodchuck_id
        sid = student.id
    client = TestClient(app)
    assert client.post("/account/login", data={"woodchuck_id": login_id, "pin": "1234"}).status_code == 200
    for phrase in ("delete", "DELÉTE", "no", "Remove", "Confirm"):
        response = client.post("/account/delete", data={"woodchuck_id": login_id, "pin": "1234", "confirmation": phrase})
        assert response.status_code == 400
    assert client.post("/account/delete", data={
        "woodchuck_id": login_id, "pin": "1234", "confirmation": "DELETE",
    }).status_code == 429
    with deletion_db() as session:
        account = session.get(WoodchuckProfile, sid)
        assert account.status == "active" and account.deletion_failed_attempts == 5


def test_deleted_invitation_token_is_invalid(deletion_db) -> None:
    with deletion_db() as session:
        deleted = profile(session, 20)
        invitation = TrustedVerifierInvitation(
            profile_id=deleted.id, email="pending@example.com", role="parent",
            token_hash=hash_invitation_token("old-token"), status="pending",
            expires_at=NOW + timedelta(days=1),
        )
        session.add(invitation); session.commit()
        anonymize_woodchuck_account(session, profile=deleted, now=NOW); session.commit()
        with pytest.raises(ValueError):
            accept_trusted_verifier_invitation(
                session, token="old-token", display_name="Parent", pin="2468"
            )


def test_retired_identifier_uses_stable_keyed_reservation(
    deletion_db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SESSION_SECRET", "stable-test-session-secret")
    old_id = "WC-PRIVATE-01"
    with deletion_db() as session:
        deleted = WoodchuckProfile(
            woodchuck_id=old_id, display_name="Private Student",
            pin_hash=hash_pin("1234"), instrument="Flute", level="Beginner",
            goal="Practice",
        )
        session.add(deleted); session.flush()
        session.add(WoodchuckState(profile_id=deleted.id, state_json={}, revision=0))
        session.commit()
        anonymize_woodchuck_account(session, profile=deleted, now=NOW)
        session.commit(); session.refresh(deleted)

        normalized = normalize_woodchuck_id(old_id)
        plain_sha = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        assert deleted.retired_woodchuck_id_hash == retired_identifier_hash(old_id)
        assert retired_identifier_hash(f"  {old_id.lower()}  ") == deleted.retired_woodchuck_id_hash
        assert deleted.retired_woodchuck_id_hash != plain_sha
        assert old_id not in deleted.woodchuck_id

        generated = iter([old_id.lower(), "WC-AVAILABLE-02"])
        monkeypatch.setattr("app.accounts.generate_woodchuck_id", lambda: next(generated))
        replacement = create_woodchuck_profile(
            session, display_name="Replacement", pin="5678", instrument="Flute",
            level="Beginner", goal="Practice",
        )
        assert replacement.woodchuck_id == "WC-AVAILABLE-02"
        assert old_id not in caplog.text
