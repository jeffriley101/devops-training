from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "templates" / "home.html").read_text()
BASE = (ROOT / "templates" / "base.html").read_text()
APP = (ROOT / "static" / "js" / "app.js").read_text()
CSS = (ROOT / "static" / "css" / "styles.css").read_text()
STORE_INVENTORY = (ROOT / "app" / "store_inventory.py").read_text()


def test_shed_has_compact_login_streak_and_weekly_crown_progress() -> None:
    assert 'id="login-streak-card"' in HOME
    assert 'id="login-streak-days"' in HOME
    assert 'id="login-streak-progress" max="7"' in HOME
    assert 'id="login-streak-progress-text"' in HOME
    assert "weekly streak crown" in HOME.casefold()
    assert ".login-streak-card" in CSS
    assert "grid-template-columns: minmax(0, 1fr);" in CSS


def test_shed_records_and_renders_server_login_streak_without_browser_inference() -> None:
    start = APP.index("  function wireLoginStreak() {")
    end = APP.index("  function updateStreak", start)
    wiring = APP[start:end]
    assert 'fetch("/account/login-streak"' in wiring
    assert 'method: "POST"' in wiring
    assert "payload.current_streak" in wiring
    assert "payload.crown_progress" in wiring
    assert "payload.dandelion_balance" in wiring
    assert "localDateKey" not in wiring
    assert 'document.body.dataset.authenticated !== "true"' in wiring
    assert "data-authenticated=" in BASE
    assert APP.count("wireLoginStreak();") == 1


def test_weekly_login_crown_has_stickerbook_presentation_name() -> None:
    assert '"weekly-login-streak": "Weekly Streak Crown"' in STORE_INVENTORY
