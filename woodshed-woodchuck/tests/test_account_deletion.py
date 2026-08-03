from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, main as main_module, verifier_routes
from app.account_deletion import DELETED_PUBLIC_NAME, anonymize_woodchuck_account
from app.account_routes import SESSION_PROFILE_ID, SESSION_PROFILE_VERSION
from app.db import Base
from app.main import app
from app.models import (
    CampPointAward, Contest, ContestResult, ContestWeek, CrownProgress,
    PracticeChart, PracticeChartVerification, PracticeEmailPreset, RewardGrant,
    Season, StudentVerifierConnection, Team, TeamMembership,
    TrustedVerifier, TrustedVerifierInvitation, WoodchuckProfile, WoodchuckState,
)
from app.security import hash_invitation_token, hash_pin
from app.verifiers import accept_trusted_verifier_invitation


NOW = datetime(2026, 8, 3, 20, tzinfo=timezone.utc)


@pytest.fixture
def deletion_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(main_module, "SessionLocal", factory)
    monkeypatch.setattr(verifier_routes, "SessionLocal", factory)
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
    team = Team(
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
        }
        anonymize_woodchuck_account(session, profile=deleted, now=NOW)
        session.commit(); session.expire_all()

        deleted = session.get(WoodchuckProfile, deleted.id)
        assert deleted.status == "deleted"
        assert deleted.deleted_at.replace(tzinfo=timezone.utc) == NOW
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
        assert session.scalar(select(TeamMembership).where(
            TeamMembership.profile_id == deleted.id
        )).ended_at.replace(tzinfo=timezone.utc) == NOW
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


def test_delete_route_requires_exact_credentials_and_revokes_cookie(deletion_db) -> None:
    with deletion_db() as session:
        deleted = profile(session, 10)
        old_id = deleted.woodchuck_id
        session.commit()
        profile_id = deleted.id
    client = TestClient(app)
    assert client.get("/account/delete").status_code == 405
    assert client.post("/account/delete", data={
        "woodchuck_id": old_id, "pin": "1234", "confirmation": "DELETE",
    }).status_code == 401
    login = client.post("/account/login", data={"woodchuck_id": old_id, "pin": "1234"})
    assert login.status_code == 200
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
    assert "Your Woodchuck account has been deleted." in response.text
    assert client.get("/account/me").json()["authenticated"] is False
    assert second.get("/account/me").json()["authenticated"] is False
    assert client.post("/account/login", data={
        "woodchuck_id": old_id, "pin": "1234"
    }).status_code == 401
    with deletion_db() as session:
        row = session.get(WoodchuckProfile, profile_id)
        assert row.status == "deleted" and row.retired_woodchuck_id_hash


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
