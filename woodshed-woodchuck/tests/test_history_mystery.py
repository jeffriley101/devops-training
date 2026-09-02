from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, arcade_routes, main
from app.arcade_rewards import (
    ARCADE_PAYOUT_THRESHOLDS,
    ArcadeDailyLimitError,
    arcade_play_status,
    complete_arcade_play,
    payout_for_score,
    start_arcade_play,
)
from app.db import Base
from app.history_mystery import (
    HISTORY_MYSTERY_CATEGORIES,
    HISTORY_MYSTERY_INSTRUMENTS,
    HISTORY_MYSTERY_QUESTIONS,
    history_mystery_questions_for_date,
)
from app.main import app
from app.models import ArcadeHighScore, ArcadePlaySession, WoodchuckProfile, WoodchuckState
from app.security import hash_pin


ROOT = Path(__file__).resolve().parents[1]
ARCADE = (ROOT / "templates" / "arcade.html").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "history_mystery.html").read_text(
    encoding="utf-8"
)
GAME_JS = (ROOT / "static" / "js" / "history-mystery.js").read_text(
    encoding="utf-8"
)
ARCADE_JS = (ROOT / "static" / "js" / "arcade.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")


def run_history_node(body: str) -> dict[str, object]:
    source = f"""
const assert = require("node:assert/strict");
const {{ HistoryMysteryGame, HISTORY_MYSTERY_RULES }} =
  require("./static/js/history-mystery.js");
{body}
"""
    result = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout or "{}")


@pytest.fixture()
def history_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(arcade_routes, "SessionLocal", factory)
    monkeypatch.setattr(main, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_profile(factory, suffix: str, *, credits: int = 20) -> WoodchuckProfile:
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-HISTORY-{suffix}",
            display_name=f"History {suffix}",
            pin_hash=hash_pin("2468"),
            instrument="Flute",
            level="Beginner",
            goal="Practice",
            status="active",
        )
        session.add(profile)
        session.flush()
        session.add(WoodchuckState(
            profile_id=profile.id,
            state_json={"progress": {"credits": credits}},
            revision=0,
        ))
        session.commit()
        return profile


def signed_client(factory, suffix: str, *, credits: int = 20):
    profile = add_profile(factory, suffix, credits=credits)
    client = TestClient(app)
    assert client.post(
        "/account/login",
        data={"woodchuck_id": profile.woodchuck_id, "pin": "2468"},
    ).status_code == 200
    with factory() as session:
        state = session.get(WoodchuckState, profile.id)
        state.state_json = {"progress": {"credits": credits}}
        session.commit()
    return client, profile


def test_ninth_cabinet_and_authenticated_history_route(history_database) -> None:
    assert ARCADE.count('class="arcade-cabinet ') == 9
    assert 'class="arcade-cabinet arcade-cabinet-history"' in ARCADE
    assert 'href="/arcade/history-mystery"' in ARCADE
    assert 'data-arcade-personal-best="history-mystery"' in ARCADE
    assert '"history-mystery": "/arcade/scores/history-mystery"' in ARCADE_JS
    anonymous = TestClient(app).get(
        "/arcade/history-mystery", follow_redirects=False
    )
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    client, _profile = signed_client(history_database, "ROUTE")
    response = client.get("/arcade/history-mystery")
    assert response.status_code == 200
    assert "Five questions. One try today." in response.text
    assert 'data-history-mystery-game' in response.text
    assert 'data-arcade-leaderboard="history-mystery"' not in TEMPLATE


def test_history_mystery_uses_thunderpants_through_shared_soundtrack() -> None:
    soundtrack = (ROOT / "static" / "js" / "arcade-soundtrack.js").read_text(
        encoding="utf-8"
    )
    assert 'data-arcade-soundtrack="history-mystery"' in TEMPLATE
    assert 'data-arcade-soundtrack-toggle' in TEMPLATE
    assert '/static/js/arcade-soundtrack.js?v=6' in TEMPLATE
    assert 'url: "/static/audio/arcade/thunderpants.mp3?v=1"' in soundtrack
    assert (ROOT / "static" / "audio" / "arcade" / "thunderpants.mp3").is_file()


def test_curated_question_bank_is_valid_complete_and_sourced() -> None:
    assert len(HISTORY_MYSTERY_QUESTIONS) == 26
    ids = [question["id"] for question in HISTORY_MYSTERY_QUESTIONS]
    assert len(ids) == len(set(ids))
    assert all(isinstance(question_id, str) and question_id for question_id in ids)
    assert {question["category"] for question in HISTORY_MYSTERY_QUESTIONS} == set(
        HISTORY_MYSTERY_CATEGORIES
    )
    assert {question["instrument"] for question in HISTORY_MYSTERY_QUESTIONS} == set(
        HISTORY_MYSTERY_INSTRUMENTS
    )
    for category in HISTORY_MYSTERY_CATEGORIES:
        count = sum(
            question["category"] == category
            for question in HISTORY_MYSTERY_QUESTIONS
        )
        assert 4 <= count <= 6
    for question in HISTORY_MYSTERY_QUESTIONS:
        choices = question["choices"]
        assert len(choices) in (3, 4)
        assert len(choices) == len(set(choices))
        assert choices.count(question["answer"]) == 1
        assert question["prompt"]
        assert question["fact"]
        assert str(question["source_url"]).startswith("https://")


def test_daily_question_selection_is_deterministic_ordered_and_unique() -> None:
    play_date = date(2026, 9, 1)
    first = history_mystery_questions_for_date(play_date)
    again = history_mystery_questions_for_date(play_date)
    next_day = history_mystery_questions_for_date(date(2026, 9, 2))
    assert [question["id"] for question in first] == [
        question["id"] for question in again
    ]
    assert [question["category"] for question in first] == list(
        HISTORY_MYSTERY_CATEGORIES
    )
    assert len({question["id"] for question in first}) == 5
    assert [question["id"] for question in next_day] != [
        question["id"] for question in first
    ]


def test_client_accepts_each_answer_once_and_completes_after_five() -> None:
    result = run_history_node("""
const categories = HISTORY_MYSTERY_RULES.categoryOrder;
const questions = categories.map((category, index) => ({
  id: `q-${index}`, category, prompt: `Question ${index + 1}`,
  choices: ["A", "B", "C"], answer: index % 2 ? "B" : "A", fact: "Fact",
}));
const game = new HistoryMysteryGame(questions);
game.start();
let result = game.answer("A");
assert.equal(result.accepted, true);
assert.equal(result.correct, true);
assert.deepEqual(game.answer("A"), { accepted: false, reason: "not-ready" });
for (let index = 1; index < 5; index += 1) {
  assert.equal(game.advance(), true);
  result = game.answer(index % 2 ? "C" : "A");
}
assert.equal(game.status, "ended");
assert.equal(game.score, 3);
assert.equal(result.finished, true);
assert.deepEqual(game.answer("A"), { accepted: false, reason: "not-ready" });
assert.equal(game.markSubmitted(), true);
assert.equal(game.markSubmitted(), false);
console.log(JSON.stringify({ score: game.score, status: game.status,
  categories: HISTORY_MYSTERY_RULES.categoryOrder }));
""")
    assert result == {
        "score": 3,
        "status": "ended",
        "categories": list(HISTORY_MYSTERY_CATEGORIES),
    }
    assert "if (finishPromise) return finishPromise" in GAME_JS
    assert "if (!game.markSubmitted()) return Promise.resolve(null)" in GAME_JS


def test_daily_start_cost_limit_and_idempotent_completion(history_database) -> None:
    client, profile = signed_client(history_database, "DAILY", credits=4)
    assert client.get("/arcade/history-mystery").status_code == 200
    with history_database() as session:
        assert session.get(WoodchuckState, profile.id).state_json["progress"]["credits"] == 4

    first = client.post("/arcade/plays", json={"game_key": "history-mystery"})
    assert first.status_code == 200
    assert first.json()["balance"] == 3
    assert first.json()["entry_cost"] == 1
    assert client.get("/arcade/plays/status/history-mystery").json()[
        "daily_play_available"
    ] is False
    second = client.post("/arcade/plays", json={"game_key": "history-mystery"})
    assert second.status_code == 409
    assert "once each Central day" in second.json()["detail"]

    completed = client.post(
        f"/arcade/plays/{first.json()['play_token']}/complete", json={"score": 5}
    )
    assert completed.status_code == 200
    assert completed.json()["payout"] == 5
    retry = client.post(
        f"/arcade/plays/{first.json()['play_token']}/complete", json={"score": 5}
    )
    assert retry.status_code == 200
    assert retry.json()["already_completed"] is True
    with history_database() as session:
        state = session.get(WoodchuckState, profile.id)
        assert state.state_json["progress"]["credits"] == 8
        plays = session.scalars(select(ArcadePlaySession).where(
            ArcadePlaySession.profile_id == profile.id,
            ArcadePlaySession.game_key == "history-mystery",
        )).all()
        assert len(plays) == 1


def test_zero_balance_and_score_bounds_are_server_enforced(history_database) -> None:
    empty, empty_profile = signed_client(history_database, "EMPTY", credits=0)
    rejected = empty.post("/arcade/plays", json={"game_key": "history-mystery"})
    assert rejected.status_code == 409
    with history_database() as session:
        assert session.scalar(select(ArcadePlaySession.id)) is None
        assert session.get(WoodchuckState, empty_profile.id).state_json[
            "progress"
        ]["credits"] == 0

    client, _profile = signed_client(history_database, "BOUNDS", credits=2)
    started = client.post("/arcade/plays", json={"game_key": "history-mystery"})
    too_high = client.post(
        f"/arcade/plays/{started.json()['play_token']}/complete", json={"score": 6}
    )
    assert too_high.status_code == 404
    valid = client.post(
        f"/arcade/plays/{started.json()['play_token']}/complete", json={"score": 2}
    )
    assert valid.status_code == 200
    assert valid.json()["payout"] == 0


def test_exact_history_payout_tiers() -> None:
    assert ARCADE_PAYOUT_THRESHOLDS["history-mystery"] == (
        (3, 1), (4, 2), (5, 5)
    )
    assert [payout_for_score("history-mystery", score) for score in range(6)] == [
        0, 0, 0, 1, 2, 5
    ]


def test_central_midnight_resets_daily_eligibility(history_database) -> None:
    profile = add_profile(history_database, "MIDNIGHT", credits=3)
    before_midnight = datetime(2026, 9, 1, 4, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 9, 1, 5, 1, tzinfo=timezone.utc)
    with history_database() as session:
        first = start_arcade_play(
            session,
            profile_id=profile.id,
            game_key="history-mystery",
            now=before_midnight,
        )
        session.commit()
        with pytest.raises(ArcadeDailyLimitError):
            start_arcade_play(
                session,
                profile_id=profile.id,
                game_key="history-mystery",
                now=before_midnight,
            )
        session.rollback()
        assert arcade_play_status(
            session,
            profile_id=profile.id,
            game_key="history-mystery",
            now=before_midnight,
        )["daily_play_available"] is False
        second = start_arcade_play(
            session,
            profile_id=profile.id,
            game_key="history-mystery",
            now=after_midnight,
        )
        session.commit()
        assert first.play.daily_play_date == date(2026, 8, 31)
        assert second.play.daily_play_date == date(2026, 9, 1)


def test_database_constraint_blocks_duplicate_daily_start(history_database) -> None:
    profile = add_profile(history_database, "CONCURRENT")
    play_date = date(2026, 9, 1)
    with history_database() as session:
        session.add(ArcadePlaySession(
            profile_id=profile.id,
            game_key="history-mystery",
            play_token="history-concurrent-first-token",
            daily_play_date=play_date,
            entry_cost=1,
        ))
        session.commit()
        session.add(ArcadePlaySession(
            profile_id=profile.id,
            game_key="history-mystery",
            play_token="history-concurrent-second-token",
            daily_play_date=play_date,
            entry_cost=1,
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def test_personal_best_is_independent_and_lower_score_does_not_replace(
    history_database,
) -> None:
    profile = add_profile(history_database, "BEST", credits=4)
    first_day = datetime(2026, 8, 30, 18, tzinfo=timezone.utc)
    second_day = datetime(2026, 8, 31, 18, tzinfo=timezone.utc)
    with history_database() as session:
        first = start_arcade_play(
            session, profile_id=profile.id, game_key="history-mystery", now=first_day
        )
        session.commit()
        complete_arcade_play(
            session,
            profile_id=profile.id,
            play_token=first.play.play_token,
            score=5,
            now=first_day,
        )
        session.commit()
        second = start_arcade_play(
            session, profile_id=profile.id, game_key="history-mystery", now=second_day
        )
        session.commit()
        result = complete_arcade_play(
            session,
            profile_id=profile.id,
            play_token=second.play.play_token,
            score=3,
            now=second_day,
        )
        session.commit()
        score = session.scalar(select(ArcadeHighScore).where(
            ArcadeHighScore.profile_id == profile.id,
            ArcadeHighScore.game_key == "history-mystery",
        ))
        assert result["best_score"] == 5
        assert result["updated"] is False
        assert score.best_score == 5


def test_mobile_history_layout_has_large_wrapping_answer_targets() -> None:
    grid = CSS[CSS.index(".history-mystery-answer-grid {"):]
    grid = grid[:grid.index("}")]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in grid
    buttons = CSS[CSS.index(".history-mystery-answer-grid button {"):]
    buttons = buttons[:buttons.index("}")]
    assert "min-height: 3.4rem" in buttons
    assert "white-space: normal" in buttons
    mobile = CSS[CSS.index("@media (max-width: 430px)"):]
    assert ".history-mystery-answer-grid button { min-height: 3.6rem; }" in mobile


def test_history_migration_extends_constraints_and_adds_daily_guard() -> None:
    migration = (
        ROOT / "migrations" / "versions" /
        "j0e1f2a3b4c5_add_history_mystery_arcade_game.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "j0e1f2a3b4c5"' in migration
    assert 'down_revision = "i9d0e1f2a3b4"' in migration
    assert migration.count("'history-mystery'") >= 6
    assert 'sa.Column("daily_play_date", sa.Date(), nullable=True)' in migration
    assert "uq_arcade_play_session_profile_game_daily_date" in migration
    assert "DELETE FROM arcade_play_sessions" in migration
    assert "DELETE FROM arcade_high_scores" in migration
