from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, arcade_routes, main
from app.arcade_scores import arcade_score_payload, record_arcade_high_score
from app.db import Base
from app.main import app
from app.models import ArcadeHighScore, WoodchuckProfile, WoodchuckState
from app.security import hash_pin


ROOT = Path(__file__).resolve().parents[1]
STORE = (ROOT / "templates" / "store.html").read_text(encoding="utf-8")
ARCADE = (ROOT / "templates" / "arcade.html").read_text(encoding="utf-8")
GAME = (ROOT / "templates" / "arcade_game.html").read_text(encoding="utf-8")
PLUNGE = (ROOT / "templates" / "plunge_burrow.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")
ARCADE_JS = (ROOT / "static" / "js" / "arcade.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
SOUNDTRACK_JS = (ROOT / "static" / "js" / "arcade-soundtrack.js").read_text(encoding="utf-8")


@pytest.fixture()
def arcade_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(arcade_routes, "SessionLocal", factory)
    monkeypatch.setattr(main, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_profile(
    factory,
    suffix: str,
    *,
    display_name: str | None = None,
    status: str = "active",
) -> WoodchuckProfile:
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-ARC-{suffix}",
            display_name=display_name or f"Arcade {suffix}",
            pin_hash=hash_pin("2468"),
            instrument="Flute",
            level="Beginner",
            goal="Practice",
            status=status,
        )
        session.add(profile)
        session.flush()
        session.add(WoodchuckState(
            profile_id=profile.id,
            state_json={"progress": {"credits": 20}},
            revision=0,
        ))
        session.commit()
        return profile


def signed_client(factory, suffix: str) -> tuple[TestClient, WoodchuckProfile]:
    profile = add_profile(factory, suffix)
    client = TestClient(app)
    response = client.post(
        "/account/login",
        data={"woodchuck_id": profile.woodchuck_id, "pin": "2468"},
    )
    assert response.status_code == 200
    return client, profile


def submit_paid_score(client: TestClient, game_key: str, score: int):
    started = client.post("/arcade/plays", json={"game_key": game_key})
    assert started.status_code == 200
    return client.post(
        f"/arcade/scores/{game_key}",
        json={"score": score, "play_token": started.json()["play_token"]},
    )


def test_practice_room_destinations_are_preserved_as_three_doors() -> None:
    practice = STORE[
        STORE.index('data-shop-panel-content="practice-room"'):
        STORE.index('data-shop-panel-content="artist"')
    ]
    assert practice.count('class="practice-room-emoji-control practice-room-door"') == 3
    assert 'href="https://brassspectrogram.netlify.app/"' in practice
    assert 'target="_blank" rel="noopener noreferrer"' in practice
    assert 'aria-label="Pristine P-Chart — Coming Soon"' in practice
    assert "<small>Coming Soon</small>" not in practice
    assert 'href="/arcade" aria-label="Open Arcade Room"' in practice
    assert practice.count('class="practice-room-door-tag"') == 3
    assert practice.count('class="practice-room-door-window" aria-hidden="true"') == 3
    for letter in ("A", "B", "C"):
        assert f'class="practice-room-door-tag">{letter}</span>' in practice
    for retired_tag in ("A — Brass", "B — Pristine", "C — Arcade"):
        assert retired_tag not in practice
    door_css = CSS[CSS.index(".practice-room-door {"):CSS.index("/* Arcade Room")]
    assert "min-height: 14rem" in door_css
    assert "grid-template-rows: 2rem 3.4rem 2.25rem" in door_css
    assert "border: 4px solid" in door_css
    assert ".practice-room-door::after" in door_css
    assert ".practice-room-door-tag" in door_css
    assert ".practice-room-door-window" in door_css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in CSS
    nameplate = door_css[door_css.index(".practice-room-door strong {"):]
    assert "min-height: 2.25rem" in nameplate
    assert "box-sizing: border-box" in nameplate
    assert "place-items: center" in nameplate
    tag = door_css[door_css.index(".practice-room-door-tag {"):door_css.index(".practice-room-door-window {")]
    assert "position: static" in tag


def test_arcade_room_renders_six_touch_friendly_cabinets() -> None:
    assert ARCADE.count('class="arcade-cabinet ') == 6
    assert 'href="/plunge-burrow"' in ARCADE
    assert 'href="/arcade/blue"' in ARCADE
    assert 'href="/arcade/radio-tuner"' in ARCADE
    assert 'href="/arcade/wheel-of-woodchuck"' in ARCADE
    assert 'href="/arcade/scale-keyboard"' in ARCADE
    assert 'href="/arcade/thirds"' in ARCADE
    assert ARCADE.count("<h2>Top 5</h2>") == 6
    assert ARCADE.count('class="arcade-cabinet-marquee"') == 6
    assert ARCADE.count('class="arcade-cabinet-control-panel" aria-hidden="true"') == 6
    assert ARCADE.count('data-arcade-personal-best=') == 6
    assert ARCADE.count("1🌼 TO PLAY · WIN UP TO 5🌼") == 6
    assert "arcade-cabinet-copy" not in ARCADE
    for retired_copy in (
        "Burrow, collect, and build your band.",
        "Run, jump, and collect blue sparks.",
        "Find the target signal before time runs out.",
        "Spin, spell, and solve music terms.",
        "Play the notes of each scale in order.",
    ):
        assert retired_copy not in ARCADE
    plunge_style = CSS[CSS.index(".arcade-cabinet-plunge .arcade-cabinet-screen"):]
    plunge_style = plunge_style[:plunge_style.index("}")]
    assert "#f5df68" not in plunge_style
    assert 'href="/arcade">Back to Arcade</a>' in PLUNGE
    assert 'href="/store">Back to SHOP</a>' in ARCADE
    assert "min-height: 17rem" in CSS[CSS.index(".arcade-cabinet-link {"):]
    mobile = CSS[CSS.index("@media (max-width: 760px)"):]
    assert ".arcade-cabinet-grid { grid-template-columns: 1fr; }" in mobile
    assert '/static/js/arcade.js?v=10' in ARCADE


def test_arcade_pages_route_game_specific_soundtracks() -> None:
    assert '/static/js/arcade-soundtrack.js' not in ARCADE
    for template in (GAME, PLUNGE):
        assert '/static/js/arcade-soundtrack.js?v=2' in template
    assert 'data-arcade-soundtrack="{{ arcade_game.key }}"' in GAME
    assert 'data-arcade-soundtrack="plunge-burrow"' in PLUNGE
    assert (ROOT / "static" / "audio" / "arcade" / "jeremy-9.mp3").is_file()
    assert (ROOT / "static" / "audio" / "arcade" / "gerry-3.mp3").is_file()
    assert (ROOT / "static" / "audio" / "arcade" / "trouble.mp3").is_file()
    assert '"plunge-burrow": {' in SOUNDTRACK_JS
    assert 'url: "/static/audio/arcade/jeremy-9.mp3?v=1"' in SOUNDTRACK_JS
    assert 'blue: {' in SOUNDTRACK_JS
    assert 'url: "/static/audio/arcade/gerry-3.mp3?v=1"' in SOUNDTRACK_JS
    assert 'loop: true' in SOUNDTRACK_JS
    assert '"radio-tuner": {' in SOUNDTRACK_JS
    assert 'url: "/static/audio/arcade/trouble.mp3?v=1"' in SOUNDTRACK_JS
    assert "const RESTART_DELAY_MS = 6000" in SOUNDTRACK_JS
    assert "audio.loop = soundtrack.loop === true" in SOUNDTRACK_JS
    assert 'if (!audio.loop) audio.addEventListener("ended", restartAfterPause)' in SOUNDTRACK_JS
    assert 'audio.addEventListener("ended", restartAfterPause)' in SOUNDTRACK_JS
    assert "window.setTimeout(function ()" in SOUNDTRACK_JS
    assert "window.WoodshedAudio" in SOUNDTRACK_JS
    assert "localStorage" not in SOUNDTRACK_JS
    assert "sessionStorage" not in SOUNDTRACK_JS


def test_arcade_games_use_the_shared_soundtrack_toggle() -> None:
    for template in (GAME, PLUNGE):
        assert 'data-arcade-soundtrack-toggle' in template
        assert 'class="arcade-soundtrack-toggle"' in template
    assert "window.WoodshedAudio.setEnabled" in SOUNDTRACK_JS
    assert "updateSoundtrackToggle" in SOUNDTRACK_JS
    assert "syncSoundtrackPreference" in SOUNDTRACK_JS


def test_arcade_landing_renders_personal_bests_from_existing_score_payload() -> None:
    room = ARCADE_JS[ARCADE_JS.index("function wireArcadeRoom"):ARCADE_JS.index("function wireArcadeGame")]
    assert "function renderPersonalBest" in ARCADE_JS
    assert "data-arcade-personal-best" in ARCADE_JS
    assert "renderPersonalBest(list.dataset.arcadeLeaderboard, payload.best_score)" in room


def test_arcade_leaderboards_render_each_olympic_rank_once() -> None:
    renderer = ARCADE_JS[
        ARCADE_JS.index("function renderLeaderboard"):
        ARCADE_JS.index("async function loadScores")
    ]
    assert "item.value = Number(row.rank)" in renderer
    assert "item.textContent = `${name} — ${row.score}`" in renderer
    assert "${row.rank}." not in renderer
    assert ARCADE.count("<ol data-arcade-leaderboard=") == 6


def test_arcade_removes_unnecessary_copy() -> None:
    combined = ARCADE + GAME + ARCADE_JS
    assert "Woodshed Woodchuck" not in combined
    assert "Choose a cabinet. Personal bests follow your Woodchuck across devices." not in combined
    assert "Scores are unavailable." not in combined
    assert "Arcade Room" not in ARCADE + GAME
    assert "Run, jump, and collect blue sparks before time runs out." not in combined


def test_blue_is_a_timed_side_scrolling_platform_game() -> None:
    assert 'data-arcade-game="{{ arcade_game.key }}"' in GAME
    assert 'id="blue-game-canvas"' in GAME
    assert 'data-blue-action="left"' in GAME
    assert 'data-blue-action="jump"' in GAME
    assert 'data-blue-action="right"' in GAME
    assert "const BLUE_WORLD_WIDTH = 3200" in ARCADE_JS
    assert "bluePlayer.velocityY += 1350 * deltaSeconds" in ARCADE_JS
    assert "bluePlayer.velocityY = -535" in ARCADE_JS
    assert "const bluePlatforms = [" in ARCADE_JS
    assert "blueCameraX" in ARCADE_JS
    assert "updateScore(10)" in ARCADE_JS
    assert 'document.addEventListener("keydown"' in ARCADE_JS
    assert 'button.addEventListener("pointerdown"' in ARCADE_JS
    assert "window.requestAnimationFrame(blueLoop)" in ARCADE_JS
    assert ".blue-game-field.is-playing * { touch-action: none; }" in CSS


def test_blue_collectibles_play_the_shared_short_pickup_effect() -> None:
    update = ARCADE_JS[
        ARCADE_JS.index("function updateBlueGame"):
        ARCADE_JS.index("function drawBlueGame")
    ]
    pickup = ARCADE_JS[
        ARCADE_JS.index("function playBluePickupSound"):
        ARCADE_JS.index("function playerFellThroughBlueFinalHole")
    ]
    assert 'window.WoodshedAudio.play("arcadePickup")' in pickup
    assert "try {" in pickup and "catch (_error)" in pickup
    assert "updateScore(10);\n          playBluePickupSound();" in update


def test_blue_final_hole_enters_one_red_stage_without_changing_geometry() -> None:
    transition = ARCADE_JS[
        ARCADE_JS.index("function playerFellThroughBlueFinalHole"):
        ARCADE_JS.index("function updateBlueGame")
    ]
    assert 'const BLUE_FINAL_HOLE = Object.freeze({ left: 2360, right: 2450 })' in ARCADE_JS
    assert 'if (blueStage === "red" || !blueFinalHoleFallInProgress) return false;' in transition
    assert "function trackBlueFinalHoleFall()" in transition
    assert 'blueStage = "red"' in transition
    assert "resetBlueStage();" in transition
    assert "grantBlueRedStageBonus();" in transition
    assert "const bluePlatforms = [" in ARCADE_JS
    assert "const redStage = blueStage === \"red\"" in ARCADE_JS


def test_blue_red_transition_keeps_score_resets_collectibles_and_other_holes_reset() -> None:
    stage_reset = ARCADE_JS[
        ARCADE_JS.index("function resetBlueStage"):
        ARCADE_JS.index("function resetBlueGame")
    ]
    update = ARCADE_JS[
        ARCADE_JS.index("function updateBlueGame"):
        ARCADE_JS.index("function drawBlueGame")
    ]
    assert "blueCollectibles = blueCollectibleSeeds.map" in stage_reset
    assert "score = 0" not in stage_reset
    assert 'if (blueStage === "blue" && enterBlueRedStage()) return;' in update
    assert "bluePlayer.x = Math.max(0, bluePlayer.checkpointX - 35);" in update


def test_blue_timer_ends_game_and_submits_score() -> None:
    assert 'id="arcade-game-time">{% if arcade_game.key == "blue" %}20{% else %}30{% endif %}' in GAME
    assert "Start {% if arcade_game.key == \"blue\" %}20{% else %}30{% endif %}-Second Game" in GAME
    assert "const BLUE_GAME_SECONDS = 20" in ARCADE_JS
    assert "const RADIO_GAME_SECONDS = 30" in ARCADE_JS
    assert "endTime = performance.now() + BLUE_GAME_SECONDS * 1000" in ARCADE_JS
    assert "if (remaining <= 0) void finishGame()" in ARCADE_JS
    assert "timeEl.textContent = \"0\"" in ARCADE_JS
    assert "WoodshedArcadeEconomy.completePlay" in ARCADE_JS
    finish = ARCADE_JS[ARCADE_JS.index("async function finishGame"):ARCADE_JS.index("function updateTimer")]
    assert 'new CustomEvent("woodshed:celebrate"' in finish
    assert finish.index('message.textContent = `Time! You scored') < finish.index("WoodshedArcadeEconomy.completePlay")
    assert 'window.addEventListener("woodshed:celebrate"' in APP_JS
    assert "celebrateSuccess(document.body)" in APP_JS


def test_blue_red_stage_bonus_adds_ten_seconds_once_without_resetting_timer() -> None:
    bonus = ARCADE_JS[
        ARCADE_JS.index("function grantBlueRedStageBonus"):
        ARCADE_JS.index("function setBlueAction")
    ]
    assert "const BLUE_RED_STAGE_BONUS_SECONDS = 10" in ARCADE_JS
    assert "blueRedStageBonusApplied" in bonus
    assert "endTime += BLUE_RED_STAGE_BONUS_SECONDS * 1000" in bonus
    assert "endTime = BLUE_RED_STAGE_BONUS_SECONDS" not in bonus
    assert "return false" in bonus


def test_red_timeout_saves_once_then_returns_to_book_without_changing_normal_blue_timeout() -> None:
    finish = ARCADE_JS[
        ARCADE_JS.index("async function finishGame"):
        ARCADE_JS.index("function updateTimer")
    ]
    assert "if (!running) return;" in finish
    assert "running = false;" in finish
    assert "WoodshedArcadeEconomy.completePlay" in finish
    assert 'gameKey === "blue" && blueStage === "red"' in finish
    assert 'window.location.assign("/p-book")' in finish
    assert "blueRedBookRedirectScheduled" in finish


def test_radio_ports_the_khjw_moving_needle_gameplay() -> None:
    assert 'class="radio-game-track"' in GAME
    assert 'class="radio-game-target"' in GAME
    assert 'id="radio-game-needle"' in GAME
    assert 'id="radio-game-tap"' in GAME
    assert "Tap / Tune" in GAME
    assert "const RADIO_TICK_MS = 40" in ARCADE_JS
    assert "const RADIO_CENTER = 50" in ARCADE_JS
    assert "const RADIO_GOLD_ZONE = 8" in ARCADE_JS
    assert "const RADIO_MAX_SCORE = 50000" in ARCADE_JS
    assert "radioNeedlePosition += radioNeedleVelocity" in ARCADE_JS
    assert "radioNeedlePosition >= 98 || radioNeedlePosition <= 2" in ARCADE_JS
    assert "const speedBoost = 1 + secondsElapsed / 42" in ARCADE_JS
    assert "0.003 * speedBoost" in ARCADE_JS


def test_radio_ports_khjw_scoring_feedback_and_30_second_timer() -> None:
    tuner = ARCADE_JS[
        ARCADE_JS.index("function tuneRadioSignal"):
        ARCADE_JS.index("async function finishGame")
    ]
    assert "Math.abs(radioNeedlePosition - RADIO_CENTER)" in tuner
    assert "Math.round((RADIO_GOLD_ZONE - distance) * 18)" in tuner
    assert "score += 100 + bonus" in tuner
    assert "score += 25" in tuner
    assert "score = Math.max(0, score - 20)" in tuner
    assert "score = Math.min(score, RADIO_MAX_SCORE)" in tuner
    for message in ("Locked!", "Close. Static cleared a little.", "Static."):
        assert message in tuner
    assert 'radioTapButton.addEventListener("click", tuneRadioSignal)' in ARCADE_JS
    assert "radioTapButton.focus()" in ARCADE_JS
    assert "elapsedMs >= RADIO_GAME_SECONDS * 1000" in ARCADE_JS
    assert "window.setInterval(tickRadioGame, RADIO_TICK_MS)" in ARCADE_JS
    assert "AudioContext" not in ARCADE_JS
    assert "createOscillator" not in ARCADE_JS
    assert "KHJW" not in ARCADE_JS


def test_arcade_pages_render_and_client_guard_includes_them(arcade_database) -> None:
    client, _profile = signed_client(arcade_database, "PAGES")
    for path in ("/arcade", "/arcade/blue", "/arcade/radio-tuner", "/plunge-burrow"):
        assert client.get(path).status_code == 200
    for path in ('"/arcade"', '"/arcade/blue"', '"/arcade/radio-tuner"'):
        assert path in APP_JS


def test_blue_and_radio_score_posts_persist_and_refresh_leaderboards(
    arcade_database,
) -> None:
    client, profile = signed_client(arcade_database, "POSTS")
    blue = submit_paid_score(client, "blue", 70)
    radio = submit_paid_score(client, "radio-tuner", 85)

    assert blue.status_code == radio.status_code == 200
    assert blue.json()["best_score"] == 70
    assert radio.json()["best_score"] == 85
    assert blue.json()["leaderboard"][0]["is_current_user"] is True
    assert radio.json()["leaderboard"][0]["is_current_user"] is True
    with arcade_database() as session:
        rows = session.scalars(
            select(ArcadeHighScore).where(ArcadeHighScore.profile_id == profile.id)
        ).all()
    assert {(row.game_key, row.best_score) for row in rows} == {
        ("blue", 70),
        ("radio-tuner", 85),
    }


def test_first_score_persists_and_lower_score_cannot_replace_it(
    arcade_database,
) -> None:
    profile = add_profile(arcade_database, "FIRST")
    with arcade_database() as session:
        first = record_arcade_high_score(
            session, profile_id=profile.id, game_key="blue", score=18
        )
        lower = record_arcade_high_score(
            session, profile_id=profile.id, game_key="blue", score=7
        )
        session.commit()
        rows = session.scalars(select(ArcadeHighScore)).all()
    assert first == (18, True)
    assert lower == (18, False)
    assert len(rows) == 1 and rows[0].best_score == 18


def test_higher_and_duplicate_submissions_are_safe_across_sessions(
    arcade_database,
) -> None:
    first_device, profile = signed_client(arcade_database, "DEVICE")
    second_device = TestClient(app)
    assert second_device.post(
        "/account/login",
        data={"woodchuck_id": profile.woodchuck_id, "pin": "2468"},
    ).status_code == 200

    assert submit_paid_score(first_device, "radio-tuner", 30).json()["updated"] is True
    assert submit_paid_score(first_device, "radio-tuner", 30).json()["updated"] is False
    higher = submit_paid_score(second_device, "radio-tuner", 55)
    reloaded = first_device.get("/arcade/scores/radio-tuner")

    assert higher.status_code == reloaded.status_code == 200
    assert higher.json()["updated"] is True
    assert reloaded.json()["best_score"] == 55


def test_top_five_uses_olympic_ties_and_public_active_names(
    arcade_database,
) -> None:
    scores = [
        ("CUR", "Current", 60, "active"),
        ("ONE", "Leader", 100, "active"),
        ("TWO", "Alpha", 90, "active"),
        ("THR", "Zulu", 90, "active"),
        ("FOR", "Fourth", 80, "active"),
        ("FIV", "Fifth", 70, "active"),
        ("SIX", "Sixth", 50, "active"),
        ("DEL", "Deleted Secret", 999, "deleted"),
    ]
    profiles = []
    for suffix, name, score, status in scores:
        profile = add_profile(
            arcade_database, suffix, display_name=name, status=status
        )
        profiles.append(profile)
        with arcade_database() as session:
            session.add(ArcadeHighScore(
                profile_id=profile.id, game_key="blue", best_score=score
            ))
            session.commit()

    with arcade_database() as session:
        payload = arcade_score_payload(
            session, profile_id=profiles[0].id, game_key="blue"
        )

    assert [(row["rank"], row["display_name"], row["score"]) for row in payload["leaderboard"]] == [
        (1, "Leader", 100),
        (2, "Alpha", 90),
        (2, "Zulu", 90),
        (4, "Fourth", 80),
        (5, "Fifth", 70),
    ]
    assert "Deleted Secret" not in str(payload)
    assert "woodchuck_id" not in str(payload)
    assert "profile_id" not in str(payload)


def test_score_api_requires_authentication_and_rejects_unknown_games(
    arcade_database,
) -> None:
    anonymous = TestClient(app)
    assert anonymous.get("/arcade/scores/blue").status_code == 401
    assert anonymous.post(
        "/arcade/scores/blue", json={"score": 5, "play_token": "x" * 20}
    ).status_code == 401

    client, _profile = signed_client(arcade_database, "AUTH")
    assert client.get("/arcade/scores/not-a-game").status_code == 404
    assert client.post(
        "/arcade/scores/not-a-game", json={"score": 5, "play_token": "x" * 20}
    ).status_code == 404
    assert client.post(
        "/arcade/scores/blue", json={"score": -1, "play_token": "x" * 20}
    ).status_code == 422


def test_arcade_migration_follows_current_head() -> None:
    migration = (
        ROOT / "migrations" / "versions" /
        "f1a2b3c4d5e6_add_arcade_high_scores.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "f1a2b3c4d5e6"' in migration
    assert 'down_revision = "e0f1a2b3c4d5"' in migration
    assert '"arcade_high_scores"' in migration
