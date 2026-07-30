from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.account_routes import profile_payload
from app.accounts import create_woodchuck_profile, update_profile_level
from app.content import LEVEL_OPTIONS
from app.db import Base
from app.models import WoodchuckProfile


ROOT = Path(__file__).resolve().parents[1]


EXPECTED_LEVELS = [
    "Beginner", "Intermediate", "Advanced", "High School", "Honors",
    "College", "Weekend Warrior", "Professional", "Legend",
    "Mount Rushmore",
]


def test_exact_level_catalog_is_used_by_account_and_profile_ui() -> None:
    assert LEVEL_OPTIONS == EXPECTED_LEVELS
    setup = TestClient(main.app).get("/setup").text
    home = TestClient(main.app).get("/home").text
    for level in EXPECTED_LEVELS:
        assert f'<option value="{level}">{level}</option>' in setup
        assert f'<option value="{level}">{level}</option>' in home
    assert "Conservatory" not in setup + home
    assert EXPECTED_LEVELS.index("Honors") < EXPECTED_LEVELS.index("College")


def test_unsupported_new_level_is_rejected_without_rewriting_existing() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        with pytest.raises(ValueError, match="supported level"):
            create_woodchuck_profile(
                session, display_name="Student", pin="1234", instrument="Flute",
                level="Wizard", goal="Practice",
            )
        profile = WoodchuckProfile(
            woodchuck_id="WC-LEGACY", display_name="Legacy", pin_hash="private",
            instrument="Flute", level="Legacy Saved Level", goal="Practice",
        )
        session.add(profile)
        session.commit()
        assert update_profile_level(
            session, profile=profile, level="Legacy Saved Level"
        ).level == "Legacy Saved Level"
        with pytest.raises(ValueError, match="supported level"):
            update_profile_level(session, profile=profile, level="Wizard")
        assert profile.level == "Legacy Saved Level"


def test_shed_profile_displays_are_semantic_keyboard_controls() -> None:
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")
    for element_id, panel in (
        ("woodchuck-name-value", "change-name-panel"),
        ("level-value", "change-level-panel"),
        ("instrument-object", "change-instrument-panel"),
    ):
        start = home.index(f'id="{element_id}"')
        opening = home.rfind("<button", 0, start)
        closing = home.index("</button>", start)
        control = home[opening:closing]
        assert 'type="button"' in control
        assert f'aria-controls="{panel}"' in control
    for old_id in (
        "change-instrument-open-button", "change-name-open-button",
        "change-level-open-button", "shed-edit-profile-row",
    ):
        assert old_id not in home
    assert 'addEventListener("click"' in account_js


def test_shed_uses_server_member_date_board_clipboard_and_compact_level() -> None:
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")

    assert "Chuckling" not in home + app_js
    assert "Member Since" in home
    assert 'aria-label="Member since {{ member_since.full }}"' in home
    assert ">📋<" in home
    assert ">📔<" not in home
    assert "profileLevel.charAt(0).toUpperCase()" in app_js
    assert 'Level: ${profileLevel}. Change level.' in app_js
    assert 'kind === "level"' in account_js


def test_profile_payload_preserves_authoritative_creation_timestamp() -> None:
    created_at = datetime(2024, 2, 3, 4, 5, 6, 789012, tzinfo=timezone.utc)
    profile = WoodchuckProfile(
        id=7, woodchuck_id="WC-DATE", display_name="Date", pin_hash="private",
        instrument="Flute", level="Intermediate", goal="Practice",
        created_at=created_at,
    )

    assert profile_payload(profile)["created_at"] == created_at.isoformat()


def test_practice_room_is_local_expandable_and_has_tool_slots() -> None:
    store = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    assert "Practice Room" in store
    assert "🚪" in store
    assert 'aria-controls="practice-room-panel"' in store
    assert "Trombone Practice Tool" in store
    assert "More Practice Tools" in store
    assert store.count("Coming Soon") >= 2
    practice_section = store[store.index("practice-room-hub"):store.index("shop-share-card")]
    assert "href=" not in practice_section
    assert "http://" not in practice_section and "https://" not in practice_section


def test_donate_moved_once_to_shop_and_qr_is_accessible() -> None:
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    store = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    assert "venmo.com/u/jeffriley101" not in home
    assert store.count("venmo.com/u/jeffriley101") == 1
    assert "Donate — Support the Shed Project" in store
    assert 'alt="QR code for the Woodshed Woodchuck website"' in store
    assert "Open the Woodshed website" in store
    assert 'data-public-site-url="{{ public_site_url }}"' in store


def test_qr_receives_only_configured_public_site_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://woodshed.example/app/?private=ignored")
    monkeypatch.setattr(main, "qr_data_uri", lambda value: captured.append(value) or "data:image/svg+xml;base64,SAFE")
    response = TestClient(main.app).get("/store")
    assert response.status_code == 200
    assert captured == ["https://woodshed.example/app/"]
    assert "private=ignored" not in response.text
    assert "Website address copied" in (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "navigator.clipboard.writeText(address)" in (ROOT / "static/js/app.js").read_text(encoding="utf-8")


def test_metronome_has_one_unaccented_sound() -> None:
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert "isAccent" not in javascript
    assert "1250" not in javascript
    assert "0.24" not in javascript
    assert "Beat one is accented" not in javascript + home
    assert ".metronome-pulse.is-accent" not in css
    assert "setValueAtTime(850, scheduledTime)" in javascript


def test_bonus_challenge_and_success_confetti_hooks() -> None:
    board = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert "Practice Challenge" not in board
    assert "Bonus Challenge" in board and "🏆" in board
    assert "bonus-challenge-section" in board and ".board-practice-section.bonus-challenge-section" in css
    assert 'persistCampPoint("trivia")' in javascript
    assert "persistedAward.created === true" in javascript
    wrong_branch = javascript[javascript.index("} else {", javascript.index('persistCampPoint("trivia")')):javascript.index("stateApi.saveState(next)", javascript.index('persistCampPoint("trivia")'))]
    assert "celebrateSuccess" not in wrong_branch
    assert "createdPayload.created === true" in javascript
    assert 'celebrateSuccess(form)' in javascript
    assert '(prefers-reduced-motion: reduce)' in javascript
    assert "pointer-events: none" in css


def test_no_conflict_markers_in_project_sources() -> None:
    for path in ROOT.rglob("*"):
        if not path.is_file() or {".git", ".venv", "__pycache__"}.intersection(path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        assert not any(line.startswith("<" * 7) or line == "=" * 7 or line.startswith(">" * 7) for line in lines)
