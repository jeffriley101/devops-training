from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import account_routes, contests
from app.db import Base
from app.main import app
from app.models import (
    CampPointAward,
    ContestResult,
    CrownProgress,
    PracticeChart,
    PracticeChartVerification,
    QuestCompletion,
    RewardGrant,
    WoodchuckState,
)


def _database(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'played-it.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(account_routes, "SessionLocal", sessions)
    monkeypatch.setattr(contests, "SessionLocal", sessions)
    return engine, sessions


def _student(client: TestClient) -> int:
    response = client.post("/account/create", data={
        "display_name": "Bonus Tester",
        "pin": "2468",
        "instrument": "Flute",
        "level": "Beginner",
        "goal": "Practice",
        "initial_state": '{"version":4,"profile":{},"progress":{"credits":7}}',
    })
    assert response.status_code == 200
    return response.json()["profile"]["id"]


def test_i_played_it_grants_only_the_daily_reward(tmp_path, monkeypatch) -> None:
    engine, sessions = _database(tmp_path, monkeypatch)
    with TestClient(app) as client:
        profile_id = _student(client)
        response = client.post("/contests/bonus-challenge/i-played-it")
        assert response.status_code == 200
        payload = response.json()
        assert payload["created"] is True
        assert payload["dandelions_awarded"] == 5
        assert payload["camp_points_awarded"] == 2
        assert payload["credits"] == 12
        assert payload["weekly_points"] == 2
        assert payload["career_points"] == 2

        with sessions() as session:
            grants = session.scalars(select(RewardGrant)).all()
            points = session.scalars(select(CampPointAward)).all()
            assert len(grants) == 1
            assert (grants[0].reward_type, grants[0].amount) == ("dandelion", 5)
            assert len(points) == 1
            assert points[0].points_awarded == 2
            assert points[0].activity_type == "bonus-i-played-it"
            assert points[0].team_id is None
            assert session.scalar(select(func.count()).select_from(PracticeChart)) == 0
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
            assert session.scalar(select(func.count()).select_from(ContestResult)) == 0
            assert session.scalar(select(func.count()).select_from(CrownProgress)) == 0
            assert session.scalar(
                select(func.count()).select_from(PracticeChartVerification)
            ) == 0
            state = session.get(WoodchuckState, profile_id)
            assert state.state_json["progress"]["credits"] == 12
            assert not state.state_json.get("practiceLog")
            assert state.state_json.get("daily", {}).get("completed") is not True
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_i_played_it_is_idempotent_across_tabs(tmp_path, monkeypatch) -> None:
    engine, sessions = _database(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _student(client)
        cookie = client.cookies.get("session")

        def claim():
            with TestClient(app) as tab:
                tab.cookies.set("session", cookie)
                return tab.post("/contests/bonus-challenge/i-played-it")

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: claim(), range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        assert sorted(response.json()["created"] for response in responses) == [False, True]
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 1
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 1
            state = session.scalar(select(WoodchuckState))
            assert state.state_json["progress"]["credits"] == 12
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_i_played_it_uses_central_calendar_date_and_reopens_next_day(
    tmp_path, monkeypatch,
) -> None:
    engine, sessions = _database(tmp_path, monkeypatch)
    first_now = datetime(2026, 8, 3, 4, 59, tzinfo=timezone.utc)

    class FirstClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return first_now if tz is not None else first_now.replace(tzinfo=None)

    monkeypatch.setattr(contests, "datetime", FirstClock)
    with TestClient(app) as client:
        _student(client)
        first = client.post("/contests/bonus-challenge/i-played-it").json()
        assert first["activity_date"] == "2026-08-02"
        assert client.post("/contests/bonus-challenge/i-played-it").json()["created"] is False

        second_now = first_now + timedelta(minutes=2)

        class SecondClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return second_now if tz is not None else second_now.replace(tzinfo=None)

        monkeypatch.setattr(contests, "datetime", SecondClock)
        second = client.post("/contests/bonus-challenge/i-played-it").json()
        assert second["activity_date"] == "2026-08-03"
        assert second["created"] is True
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 2
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 2
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_normal_quest_completion_route_remains_separate() -> None:
    source = open(contests.__file__, encoding="utf-8").read()
    assert '@router.post("/quest/completions")' in source
    assert '@router.post("/bonus-challenge/i-played-it")' in source
    assert "QuestCompletion(" not in source[
        source.index('def claim_i_played_it'):source.index('@router.post("/quest/completions")')
    ]
