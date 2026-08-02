from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app import account_routes, contests
from app.db import Base
from app.main import app
from app.instruments import canonical_instrument_key
from app.models import (
    CampPointAward,
    CrownProgress,
    PracticeChart,
    QuestCompletion,
    RewardGrant,
    WoodchuckProfile,
    WoodchuckState,
)


CENTRAL = ZoneInfo("America/Chicago")


@pytest.fixture()
def quest_database(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quest-completion.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(account_routes, "SessionLocal", local_session)
    monkeypatch.setattr(contests, "SessionLocal", local_session)
    yield local_session
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_student(
    client: TestClient, *, credits: int = 7, instrument: str = "Flute"
) -> int:
    initial_state = {
        "version": 4,
        "account": {"authenticated": False, "serverRevision": 0},
        "profile": {},
        "progress": {"credits": credits, "streak": 0, "lastCompletedDate": None},
        "practiceLog": [],
        "daily": {},
        "quest": {},
    }
    response = client.post("/account/create", data={
        "display_name": "Quest Tester",
        "pin": "2468",
        "instrument": instrument,
        "level": "Beginner",
        "goal": "Build daily consistency",
        "initial_state": json.dumps(initial_state),
    })
    assert response.status_code == 200
    return response.json()["profile"]["id"]


def completion_payload() -> dict[str, object]:
    return {
        "activity_date": datetime.now(CENTRAL).date().isoformat(),
        "quest_id": "flute-trill",
        "minutes": 10,
        "logged_minutes": 10,
        "note": "Long tones and a clean trill",
    }


def test_existing_quest_rules_define_both_reward_amounts_and_no_camp_activity() -> None:
    rewards = {
        quest["reward_credits"]
        for quests in contests.QUEST_POOL.values()
        for quest in quests
    }
    assert rewards == {15, 20}  # Task configuration remains unchanged.
    assert "quest" not in contests.CAMP_POINT_ACTIVITIES
    assert "quest" not in contests.ACTIVITY_CROWN_KEYS


@pytest.mark.parametrize("value", [
    "Piano/Keyboard", "Piano / Keyboard", "Piano", "Keyboard",
    "piano-keyboard", "  PIANO / keyboard  ",
])
def test_piano_keyboard_aliases_share_one_canonical_key(value: str) -> None:
    assert canonical_instrument_key(value) == "piano-keyboard"


def test_unrelated_instruments_keep_distinct_canonical_keys() -> None:
    assert canonical_instrument_key("Flute") == "flute"
    assert canonical_instrument_key("Accordion") == "accordion"
    assert canonical_instrument_key("Flute") != "piano-keyboard"


def test_piano_keyboard_bonus_configuration_is_approved_copy() -> None:
    challenge = contests.QUEST_POOL["piano-keyboard"][0]
    assert challenge == {
        "id": "piano-keyboard-scale-chord-progression",
        "text": "Practice a scale or chord progression with both hands.",
        "target_minutes": 10,
        "reward_credits": 15,
    }


def test_full_completion_route_persists_reward_once_and_returns_authority(
    quest_database,
) -> None:
    with TestClient(app) as client:
        profile_id = create_student(client)
        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 0
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0

        first = client.post("/contests/quest/completions", json=completion_payload())
        assert first.status_code == 200
        payload = first.json()
        assert payload["created"] is True
        assert payload["reward_created"] is True
        assert payload["camp_point_created"] is True
        assert payload["completion"]["reward_amount"] == 5
        assert payload["credits"] == 12
        assert payload["weekly_points"] == 2
        assert payload["career_points"] == 2

        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 1
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 1
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 1
            assert session.scalar(select(func.count()).select_from(CrownProgress)) == 0
            completion = session.scalar(select(QuestCompletion))
            grant = session.scalar(select(RewardGrant))
            state = session.get(WoodchuckState, profile_id)
            assert (completion.quest_id, completion.reward_amount) == ("flute-trill", 5)
            assert (grant.source_key, grant.reward_type, grant.amount) == (
                f"bonus-challenge:{completion.activity_date.isoformat()}:flute-trill", "dandelion", 5,
            )
            award = session.scalar(select(CampPointAward))
            assert (award.points_awarded, award.team_id) == (2, None)
            assert state.state_json["daily"]["completed"] is True
            assert state.state_json["daily"]["loggedMinutes"] == 10
            assert state.state_json["progress"]["credits"] == 12
            assert state.state_json["practiceLog"][0]["source"] == "quest"

        duplicate = client.post("/contests/quest/completions", json=completion_payload())
        assert duplicate.status_code == 200
        assert duplicate.json()["created"] is False
        assert duplicate.json()["reward_created"] is False
        assert duplicate.json()["credits"] == 12
        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 1
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 1

        refreshed = client.get("/account/state").json()
        assert refreshed["state"]["daily"]["completed"] is True
        assert refreshed["state"]["progress"]["credits"] == 12


def test_server_grants_nothing_before_configured_threshold(quest_database) -> None:
    with TestClient(app) as client:
        create_student(client, credits=4)
        submitted = completion_payload()
        submitted["minutes"] = 4
        submitted["logged_minutes"] = 4
        response = client.post("/contests/quest/completions", json=submitted)
        assert response.status_code == 400
        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 0
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0
            assert session.scalar(select(WoodchuckState)).state_json["progress"]["credits"] == 4


def test_bonus_progress_endpoint_persists_increment_then_rewards_threshold(
    quest_database,
) -> None:
    with TestClient(app) as client:
        create_student(client, credits=7)
        today = datetime.now(CENTRAL).date().isoformat()
        current = client.get("/contests/bonus-challenge/current").json()["challenge"]
        assert current["task"] and current["target_minutes"] == 10
        first = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": today,
            "challenge_instance": current["instance_key"],
            "minutes": 4,
            "note": "First increment",
        })
        assert first.status_code == 200
        assert first.json()["logged_minutes"] == 4
        assert first.json()["completed"] is False
        assert first.json()["dandelions_awarded"] == 0
        assert first.json()["camp_points_awarded"] == 0
        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 0
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0
            assert session.scalar(select(func.count()).select_from(PracticeChart)) == 0
        refreshed = client.get("/account/state").json()["state"]
        assert refreshed["daily"]["loggedMinutes"] == 4

        completed = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": today,
            "challenge_instance": current["instance_key"],
            "minutes": 6,
            "note": "Threshold increment",
        })
        assert completed.status_code == 200
        payload = completed.json()
        assert payload["created"] is True and payload["completed"] is True
        assert payload["logged_minutes"] == 10
        assert payload["dandelions_awarded"] == 5
        assert payload["camp_points_awarded"] == 2
        assert payload["credits"] == 12

        duplicate = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": today,
            "challenge_instance": current["instance_key"],
            "minutes": 6,
        })
        assert duplicate.status_code == 200
        assert duplicate.json()["created"] is False
        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 1
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 1
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 1
            assert session.scalar(select(func.count()).select_from(PracticeChart)) == 0
            award = session.scalar(select(CampPointAward))
            assert award.points_awarded == 2 and award.team_id is None


def test_piano_keyboard_get_and_post_share_canonical_challenge(
    quest_database,
) -> None:
    with TestClient(app) as client:
        profile_id = create_student(
            client, credits=0, instrument="Piano / Keyboard"
        )
        with quest_database() as session:
            profile = session.get(WoodchuckProfile, profile_id)
            profile.instrument = "Piano/Keyboard"
            session.commit()
        current_response = client.get("/contests/bonus-challenge/current")
        assert current_response.status_code == 200
        current = current_response.json()["challenge"]
        assert current["task"] == (
            "Practice a scale or chord progression with both hands."
        )
        assert current["target_minutes"] == 10
        assert ":piano-keyboard:" in current["instance_key"]

        partial = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": current["activity_date"],
            "challenge_instance": current["instance_key"],
            "minutes": 4,
        })
        assert partial.status_code == 200
        assert partial.json()["logged_minutes"] == 4
        assert partial.json()["dandelions_awarded"] == 0
        assert partial.json()["camp_points_awarded"] == 0
        refreshed = client.get("/contests/bonus-challenge/current").json()["challenge"]
        assert refreshed["instance_key"] == current["instance_key"]
        assert refreshed["logged_minutes"] == 4

        stale = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": current["activity_date"],
            "challenge_instance": current["instance_key"].replace(
                ":piano-keyboard:", ":flute:"
            ),
            "minutes": 6,
        })
        assert stale.status_code == 409
        completed = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": current["activity_date"],
            "challenge_instance": current["instance_key"],
            "minutes": 6,
        })
        assert completed.status_code == 200
        assert completed.json()["dandelions_awarded"] == 5
        assert completed.json()["camp_points_awarded"] == 2
        duplicate = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": current["activity_date"],
            "challenge_instance": current["instance_key"],
            "minutes": 6,
        })
        assert duplicate.status_code == 200
        assert duplicate.json()["dandelions_awarded"] == 0
        assert duplicate.json()["camp_points_awarded"] == 0
        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(PracticeChart)) == 0
            award = session.scalar(select(CampPointAward).where(
                CampPointAward.profile_id == profile_id
            ))
            assert award is not None and award.team_id is None


def test_shared_resolver_replaces_stale_cross_instrument_client_state(
    quest_database,
) -> None:
    with TestClient(app) as client:
        profile_id = create_student(client, credits=0)
        today = datetime.now(CENTRAL).date().isoformat()
        with quest_database() as session:
            state = session.get(WoodchuckState, profile_id)
            state.state_json = {
                **state.state_json,
                "daily": {
                    "dateKey": today,
                    "questId": "trumpet-lip-slur",
                    "questText": "Stale trumpet challenge",
                    "targetMinutes": 10,
                    "loggedMinutes": 3,
                },
            }
            session.commit()

        current = client.get("/contests/bonus-challenge/current")
        assert current.status_code == 200
        challenge = current.json()["challenge"]
        assert challenge["challenge_id"] in {
            item["id"] for item in contests.QUEST_POOL["Flute"]
        }
        assert challenge["task"] != "Stale trumpet challenge"
        assert challenge["logged_minutes"] == 0

        stale = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": today,
            "challenge_instance": f"{today}:trumpet:trumpet-lip-slur",
            "minutes": 1,
        })
        assert stale.status_code == 409
        valid = client.post("/contests/bonus-challenge/progress", json={
            "activity_date": today,
            "challenge_instance": challenge["instance_key"],
            "minutes": 1,
        })
        assert valid.status_code == 200
        assert valid.json()["logged_minutes"] == 1


def test_genuinely_missing_bonus_configuration_is_controlled(
    quest_database, monkeypatch,
) -> None:
    without_flute = dict(contests.QUEST_POOL)
    without_flute.pop("Flute")
    monkeypatch.setattr(contests, "QUEST_POOL", without_flute)
    with TestClient(app) as client:
        create_student(client)
        payload = client.get("/contests/bonus-challenge/current").json()
        assert payload == {
            "available": False,
            "message": "No Bonus Challenge is available.",
        }


def test_multiple_tabs_are_idempotent(quest_database) -> None:
    with TestClient(app) as first_client:
        create_student(first_client, credits=0)
        session_cookie = first_client.cookies.get("session")
        assert session_cookie

        def submit_once():
            with TestClient(app) as tab:
                tab.cookies.set("session", session_cookie)
                return tab.post("/contests/quest/completions", json=completion_payload())

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: submit_once(), range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        assert sorted(response.json()["created"] for response in responses) == [False, True]
        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 1
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 1
            state = session.scalar(select(WoodchuckState))
            assert state.state_json["progress"]["credits"] == 5
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 1


def test_failed_reward_insert_rolls_back_completion_and_state(quest_database) -> None:
    with TestClient(app) as client:
        profile_id = create_student(client, credits=3)

        def reject_reward(_mapper, _connection, _target):
            raise RuntimeError("simulated reward failure")

        event.listen(RewardGrant, "before_insert", reject_reward)
        try:
            failed_client = TestClient(app, raise_server_exceptions=False)
            failed_client.cookies.set("session", client.cookies.get("session"))
            response = failed_client.post(
                "/contests/quest/completions", json=completion_payload()
            )
            assert response.status_code == 500
        finally:
            event.remove(RewardGrant, "before_insert", reject_reward)

        with quest_database() as session:
            assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
            assert session.scalar(select(func.count()).select_from(RewardGrant)) == 0
            assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0
            state = session.get(WoodchuckState, profile_id)
            assert state.state_json["progress"]["credits"] == 3
            assert state.state_json.get("daily", {}).get("completed") is not True


def test_browser_handler_is_single_request_confirmed_ui_and_audio_safe() -> None:
    app_js = (Path(__file__).resolve().parents[1] / "static/js/app.js").read_text()
    quest = app_js[app_js.index("function wireQuestForm"):app_js.index("const STORE_ITEMS")]
    assert 'fetch("/contests/quest/completions"' not in quest
    assert quest.count('fetch("/contests/bonus-challenge/progress"') == 1
    assert 'fetch("/contests/bonus-challenge/i-played-it"' not in quest
    assert 'form.addEventListener("submit"' in quest
    assert 'completeBtn.addEventListener' not in quest
    assert 'form.dataset.bonusChallengeWired === "true"' in quest
    assert "if (completionInFlight) return" in quest
    assert "stateApi.saveState(next, { sync: false })" in quest
    assert "window.WWAccountSync.syncNow" not in quest
    assert "payload.created === true && payload.reward_created === true" in quest
    assert "next.daily.completed = payload.completed === true" in quest
    assert "next.daily.loggedMinutes = payload.logged_minutes" in quest
    assert "ww:camp-points-saved" in quest
    assert "playSound(\"questCompleted\")" in quest
    assert "try {\n      if (window.WoodshedAudio)" in app_js
    assert "Choose Another Challenge" not in quest  # Labels remain template-owned.
    assert 'fetch("/contests/bonus-challenge/current"' in quest
    assert "currentChallengeInstance = challenge.instance_key" in quest
