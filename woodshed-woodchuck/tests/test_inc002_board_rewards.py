"""Prospective BOARD self-attestations: fixed value, durable daily eligibility."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sys
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import contests
from app.db import Base
from app.economy import qualified_camp_point_clause
from app.main import app
from app.models import (CampPointAward, ContestResult, ContestWeek, CrownProgress,
                        PracticeChart, PracticeChartVerification, QuestCompletion,
                        RewardGrant, WoodchuckProfile, WoodchuckState)
from app.xp import xp_sources
from tests.test_arcade_economy import signed_client


class Clock(datetime):
    # UTC September 30 is still September 29 in Central time.
    moment = datetime(2026, 9, 30, 4, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.moment.astimezone(tz) if tz else cls.moment.replace(tzinfo=None)


@pytest.fixture
def board_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MODE", "off")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_REQUIRED", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'board.db'}",
                          connect_args={"check_same_thread": False, "timeout": 30})
    @event.listens_for(engine, "connect")
    def disposable_connection(raw, record):
        raw.execute("PRAGMA synchronous=OFF")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(contests, "datetime", Clock)
    yield factory
    engine.dispose()


def today():
    return Clock.moment.astimezone(contests.CENTRAL).date().isoformat()


def ledger(factory, profile_id):
    with factory() as session:
        awards = session.scalars(select(CampPointAward).where(
            CampPointAward.profile_id == profile_id)).all()
        grants = session.scalars(select(RewardGrant).where(
            RewardGrant.profile_id == profile_id)).all()
        return {
            "awards": [(a.id, a.activity_type, a.points_awarded, a.duplicate_key, a.team_id) for a in awards],
            "grants": [(g.id, g.source_key, g.reward_type, g.amount) for g in grants],
            "credits": session.get(WoodchuckState, profile_id).state_json["progress"]["credits"],
            "quests": session.scalars(select(QuestCompletion.id)).all(),
            "practice": session.scalars(select(PracticeChart.id)).all(),
            "verification": session.scalars(select(PracticeChartVerification.id)).all(),
        }


def assigned(client):
    response = client.get("/contests/bonus-challenge/current")
    assert response.status_code == 200, response.text
    challenge = response.json()["challenge"]
    return {"activity_date": challenge["activity_date"], "challenge_instance": challenge["instance_key"]}


def assert_reward(factory, profile_id, before, points, dandelions):
    after = ledger(factory, profile_id)
    assert len(after["awards"]) == len(before["awards"]) + 1
    assert after["awards"][-1][2] == points
    assert len(after["grants"]) == len(before["grants"]) + 1
    assert after["grants"][-1][2:] == ("dandelion", dandelions)
    assert after["credits"] == before["credits"] + dandelions
    assert after["quests"] == after["practice"] == after["verification"] == []
    with factory() as session:
        assert session.scalar(select(CampPointAward.id).where(
            CampPointAward.id == after["awards"][-1][0], qualified_camp_point_clause()))
        assert xp_sources(session, profile_id=profile_id)["practice_minutes"] == 0
        assert xp_sources(session, profile_id=profile_id)["p_charts"] == 0
    return after


@pytest.mark.parametrize("activity", ["hours", "care", "marching"])
def test_daily_fixed_reward_retries_reload_and_crown_progress(board_db, activity):
    client, profile = signed_client(board_db, activity.upper())
    assert client.get("/contests/current").status_code == 200
    before = ledger(board_db, profile.id)
    payload = {"activity_type": activity, "activity_date": today()}
    response = client.post("/contests/camp-points/awards", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert response.json()["award"]["points_awarded"] == 1
    assert response.json()["credits"] == before["credits"] + 1
    assert response.json()["camp_points_this_week"] == 1
    assert response.json()["camp_points_season"] == 1
    after = assert_reward(board_db, profile.id, before, 1, 1)
    assert after["awards"][-1][3] == f"board-self-report-v2:{today()}:{activity}"
    for _ in range(3):
        retry = client.post("/contests/camp-points/awards", json=payload)
        assert retry.status_code == 200
        assert retry.json()["created"] is False
        assert retry.json()["credits"] == after["credits"]
    saved = client.get(f"/contests/camp-points/awards/{today()}").json()
    assert saved["awards"][0]["activity_type"] == activity
    assert ledger(board_db, profile.id) == after
    with board_db() as session:
        progress = session.scalar(select(CrownProgress).where(
            CrownProgress.profile_id == profile.id,
            CrownProgress.category_key == contests.ACTIVITY_CROWN_KEYS[activity]))
        assert progress.qualifying_wins == 1


@pytest.mark.parametrize("activity", ["hours", "care", "marching", "bonus"])
def test_concurrent_duplicate_http_requests_are_atomic(board_db, activity, monkeypatch):
    client, profile = signed_client(board_db, "RACE-" + activity.upper())
    payload = assigned(client) if activity == "bonus" else {"activity_type": activity, "activity_date": today()}
    path = "/contests/bonus-challenge/progress" if activity == "bonus" else "/contests/camp-points/awards"
    before = ledger(board_db, profile.id)
    # Force both transactions past the read locks before the unique award insert.
    # SQLite ignores FOR UPDATE; this exercises the losing writer's rollback.
    barrier = Barrier(2)
    original = contests.lock_state
    def synchronize(session, profile_id):
        state = original(session, profile_id)
        if not session.info.get("inc002_barrier"):
            session.info["inc002_barrier"] = True
            barrier.wait(timeout=15)
        return state
    monkeypatch.setattr(contests, "lock_state", synchronize)
    def submit(_):
        with TestClient(app) as tab:
            tab.cookies.update(client.cookies)
            return tab.post(path, json=payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert sorted(r.json()["created"] for r in responses) == [False, True]
    points, dandelions = (2, 5) if activity == "bonus" else (1, 1)
    after = assert_reward(board_db, profile.id, before, points, dandelions)
    assert all(r.json()["credits"] == after["credits"] for r in responses)


@pytest.mark.parametrize("activity", ["hours", "care", "marching", "bonus"])
@pytest.mark.parametrize("field,value", [
    ("reward", 99999), ("reward_amount", 99999), ("points", 99999),
    ("points_awarded", 99999), ("dandelions", 99999), ("dandelion", 99999),
    ("credits", 99999), ("team_id", 1), ("score", 99999),
    ("profile_id", 99999),
    ("duplicate_key", "board-self-report-v2:2026-09-29:care"),
    ("source_key", "bonus-challenge:self-report-v2:2026-09-29:forged"),
    ("quest_id", "arbitrary"), ("challenge_id", "arbitrary"),
    ("minutes", 1440), ("logged_minutes", 1440),
    ("activity_date", None), ("activity_date", True), ("activity_date", 1790742600),
    ("activity_date", []), ("activity_date", "2026-09-29T00:00:00"),
])
def test_http_extra_value_identity_and_malformed_fields_rejected(board_db, activity, field, value):
    client, profile = signed_client(board_db, "FORGED")
    payload = assigned(client) if activity == "bonus" else {"activity_type": activity, "activity_date": today()}
    path = "/contests/bonus-challenge/progress" if activity == "bonus" else "/contests/camp-points/awards"
    before = ledger(board_db, profile.id)
    result = client.post(path, json={**payload, field: value})
    assert result.status_code == 422, result.text
    assert ledger(board_db, profile.id) == before


@pytest.mark.parametrize("activity", ["hours", "care", "marching", "bonus"])
@pytest.mark.parametrize("offset", [-1, 1])
def test_only_current_central_date_is_eligible(board_db, activity, offset):
    client, profile = signed_client(board_db, "DATE")
    payload = assigned(client) if activity == "bonus" else {"activity_type": activity, "activity_date": today()}
    path = "/contests/bonus-challenge/progress" if activity == "bonus" else "/contests/camp-points/awards"
    payload["activity_date"] = (Clock.moment.astimezone(contests.CENTRAL).date() + timedelta(days=offset)).isoformat()
    before = ledger(board_db, profile.id)
    assert client.post(path, json=payload).status_code == 400
    assert ledger(board_db, profile.id) == before


@pytest.mark.parametrize("value", ["unknown", "quest", "placement", "contest-placement", "", 1, True, [], None])
def test_unknown_or_malformed_activity_cannot_mint(board_db, value):
    client, profile = signed_client(board_db, "UNKNOWN")
    before = ledger(board_db, profile.id)
    response = client.post("/contests/camp-points/awards", json={"activity_type": value, "activity_date": today()})
    assert response.status_code in (400, 422)
    assert ledger(board_db, profile.id) == before


def test_bonus_current_assignment_retries_and_no_fabricated_minutes(board_db):
    client, profile = signed_client(board_db, "BONUS")
    assert client.get("/contests/current").status_code == 200
    payload = assigned(client)
    before = ledger(board_db, profile.id)
    result = client.post("/contests/bonus-challenge/progress", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["camp_points_awarded"] == 2
    assert result.json()["dandelions_awarded"] == 5
    assert result.json()["logged_minutes"] == 0
    assert result.json()["camp_points_this_week"] == 2
    assert result.json()["camp_points_season"] == 2
    after = assert_reward(board_db, profile.id, before, 2, 5)
    assert after["awards"][-1][3] == f"bonus-challenge:self-report-v2:{today()}"
    assert len(after["awards"][-1][3]) <= 100
    assert len(after["grants"][-1][1]) <= 150
    for _ in range(3):
        retry = client.post("/contests/bonus-challenge/progress", json=payload)
        assert retry.status_code == 200, retry.text
        assert retry.json()["created"] is False
        assert retry.json()["camp_points_awarded"] == retry.json()["dandelions_awarded"] == 0
        assert retry.json()["credits"] == after["credits"]
    assert client.get("/contests/bonus-challenge/current").json()["challenge"]["completed"] is True
    assert ledger(board_db, profile.id) == after


def test_bonus_stale_changed_and_second_assignment_are_rejected(board_db, monkeypatch):
    client, profile = signed_client(board_db, "CHANGED")
    payload = assigned(client)
    before = ledger(board_db, profile.id)
    for instance in ("invented", "2026-09-28:flute:old", payload["challenge_instance"] + "x"):
        assert client.post("/contests/bonus-challenge/progress", json={**payload, "challenge_instance": instance}).status_code == 409
    assert ledger(board_db, profile.id) == before
    # A task edit with the same quest ID must invalidate the issued instance.
    original = contests.configured_bonus_challenges
    monkeypatch.setattr(contests, "configured_bonus_challenges", lambda instrument:
                        [{**q, "text": q["text"] + " Changed."} for q in original(instrument)])
    assert client.post("/contests/bonus-challenge/progress", json=payload).status_code == 409
    current = assigned(client)
    assert client.post("/contests/bonus-challenge/progress", json=current).status_code == 200
    after = ledger(board_db, profile.id)
    with board_db() as session:
        session.get(WoodchuckProfile, profile.id).instrument = "Trumpet"
        session.commit()
    changed = assigned(client)
    assert client.post("/contests/bonus-challenge/progress", json=current).status_code == 409
    assert client.post("/contests/bonus-challenge/progress", json=changed).status_code == 409
    assert ledger(board_db, profile.id) == after


def test_bonus_rechecks_instrument_after_waiting_for_profile_lock(board_db, monkeypatch):
    client, profile = signed_client(board_db, "PROFILE-LOCK")
    payload = assigned(client)
    before = ledger(board_db, profile.id)
    original = contests.lock_state
    def instrument_changed_before_lock(session, profile_id):
        with board_db() as other:
            other.get(WoodchuckProfile, profile_id).instrument = "Trumpet"
            other.commit()
        return original(session, profile_id)
    monkeypatch.setattr(contests, "lock_state", instrument_changed_before_lock)
    response = client.post("/contests/bonus-challenge/progress", json=payload)
    assert response.status_code == 409, response.text
    assert ledger(board_db, profile.id) == before


@pytest.mark.parametrize("correct", [True, False])
def test_trivia_one_attempt_and_reward_remain_unchanged(board_db, correct):
    client, profile = signed_client(board_db, "TRIVIA")
    before = ledger(board_db, profile.id)
    question = contests.trivia_question_for(Clock.moment.astimezone(contests.CENTRAL).date())
    answer = next(c["id"] for c in question["choices"] if (c["id"] == question["correct_answer_id"]) == correct)
    payload = {"activity_date": today(), "selected_answer_id": answer}
    first = client.post("/contests/trivia/answer", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["correct"] is correct
    if correct:
        after = assert_reward(board_db, profile.id, before, 1, 1)
    else:
        after = before
    retry = client.post("/contests/trivia/answer", json={**payload, "selected_answer_id": question["correct_answer_id"]})
    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["correct"] is correct
    assert ledger(board_db, profile.id) == after


@pytest.mark.parametrize("activity", ["hours", "care", "marching", "bonus"])
def test_unauthenticated_and_inactive_cannot_attest(board_db, activity):
    client, profile = signed_client(board_db, "AUTH")
    payload = assigned(client) if activity == "bonus" else {"activity_type": activity, "activity_date": today()}
    path = "/contests/bonus-challenge/progress" if activity == "bonus" else "/contests/camp-points/awards"
    before = ledger(board_db, profile.id)
    with TestClient(app) as guest:
        assert guest.post(path, json=payload).status_code == 401
    with board_db() as session:
        session.get(WoodchuckProfile, profile.id).status = "deleted"
        session.commit()
    assert client.post(path, json=payload).status_code in (401, 403)
    assert ledger(board_db, profile.id) == before


def test_legacy_exclusion_and_reading_does_not_rewrite_history(board_db):
    client, profile = signed_client(board_db, "HISTORY")
    assert client.get("/contests/current").status_code == 200
    with board_db() as session:
        rows = [(a, f"band-camp:{today()}:{a}", 1) for a in ("hours", "care", "marching", "trivia")]
        rows += [("quest", f"bonus-challenge:{today()}:flute-trill", 2),
                 ("placement", "old-placement", 3), ("contest-placement", "old-contest-placement", 2)]
        for activity, key, points in rows:
            session.add(CampPointAward(profile_id=profile.id, activity_type=activity,
                        duplicate_key=key, points_awarded=points, occurred_at=Clock.moment))
        session.add(QuestCompletion(profile_id=profile.id, activity_date=Clock.moment.astimezone(contests.CENTRAL).date(),
                    quest_id="flute-trill", logged_minutes=1440, reward_amount=20, completed_at=Clock.moment))
        session.add(RewardGrant(profile_id=profile.id, source_key="bonus-challenge:legacy", reward_type="dandelion", amount=20))
        session.commit()
        qualified = session.scalars(select(CampPointAward.activity_type).where(qualified_camp_point_clause())).all()
        assert set(qualified) == {"trivia", "placement", "contest-placement"}
    def historical_snapshot():
        with board_db() as session:
            return {model.__tablename__: [dict(row) for row in session.execute(select(model.__table__)).mappings()]
                    for model in (CampPointAward, RewardGrant, QuestCompletion, ContestWeek, ContestResult)}
    before = historical_snapshot()
    for path in ("/quest", "/contests/current", f"/contests/camp-points/awards/{today()}", "/contests/bonus-challenge/current"):
        assert client.get(path).status_code == 200
    challenge = client.get("/contests/bonus-challenge/current").json()["challenge"]
    assert challenge["completed"] is False
    assert challenge["logged_minutes"] == 0
    saved = client.get(f"/contests/camp-points/awards/{today()}").json()
    assert [award["activity_type"] for award in saved["awards"]] == ["trivia"]
    with board_db() as session:
        assert xp_sources(session, profile_id=profile.id)["board_points"] == 6
    assert historical_snapshot() == before
    before_ledger = ledger(board_db, profile.id)
    assert client.post("/contests/camp-points/awards", json={"activity_type": "care", "activity_date": today()}).status_code == 200
    after = ledger(board_db, profile.id)
    assert after["awards"][:-1] == before_ledger["awards"]
    assert after["grants"][:-1] == before_ledger["grants"]
    with board_db() as session:
        assert set(session.scalars(select(CampPointAward.activity_type).where(qualified_camp_point_clause())).all()) == {
            "trivia", "placement", "contest-placement", "care"}


def test_legacy_quest_alias_stays_closed(board_db):
    client, profile = signed_client(board_db, "ALIAS")
    challenge = client.get("/contests/bonus-challenge/current").json()["challenge"]
    before = ledger(board_db, profile.id)
    assert client.post("/contests/quest/completions", json={"activity_date": today(),
        "quest_id": challenge["challenge_id"], "minutes": 1440, "logged_minutes": 1440}).status_code == 409
    assert ledger(board_db, profile.id) == before


@pytest.mark.parametrize("activity", ["hours", "care", "marching", "bonus"])
def test_failed_balance_write_rolls_back_entire_award_then_allows_retry(board_db, monkeypatch, activity):
    client, profile = signed_client(board_db, "ROLLBACK-" + activity.upper())
    payload = assigned(client) if activity == "bonus" else {"activity_type": activity, "activity_date": today()}
    path = "/contests/bonus-challenge/progress" if activity == "bonus" else "/contests/camp-points/awards"
    before = ledger(board_db, profile.id)
    original = contests._add_dandelion
    def fail_after_balance_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Synthetic interrupted reward transaction")
    with monkeypatch.context() as patch:
        patch.setattr(contests, "_add_dandelion", fail_after_balance_write)
        with pytest.raises(RuntimeError, match="Synthetic interrupted reward transaction"):
            client.post(path, json=payload)
    assert ledger(board_db, profile.id) == before
    retry = client.post(path, json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["created"] is True
    points, dandelions = (2, 5) if activity == "bonus" else (1, 1)
    assert_reward(board_db, profile.id, before, points, dandelions)


@pytest.mark.parametrize("activity", ["hours", "care", "marching", "bonus"])
def test_next_central_day_has_new_daily_eligibility(board_db, monkeypatch, activity):
    client, profile = signed_client(board_db, "NEXT-DAY-" + activity.upper())
    path = "/contests/bonus-challenge/progress" if activity == "bonus" else "/contests/camp-points/awards"
    payload = assigned(client) if activity == "bonus" else {"activity_type": activity, "activity_date": today()}
    assert client.post(path, json=payload).status_code == 200
    before = ledger(board_db, profile.id)
    monkeypatch.setattr(Clock, "moment", Clock.moment + timedelta(days=1))
    if activity == "bonus":
        assert client.get("/contests/bonus-challenge/current").json()["challenge"]["completed"] is False
    else:
        assert client.get(f"/contests/camp-points/awards/{today()}").json()["awards"] == []
    assert client.post(path, json=payload).status_code == 400
    current = assigned(client) if activity == "bonus" else {"activity_type": activity, "activity_date": today()}
    response = client.post(path, json=current)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    points, dandelions = (2, 5) if activity == "bonus" else (1, 1)
    assert_reward(board_db, profile.id, before, points, dandelions)
