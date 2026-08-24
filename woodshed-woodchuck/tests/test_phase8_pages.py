from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_home_exact_button_order_labels_and_destinations() -> None:
    html = (ROOT / "templates/welcome.html").read_text(encoding="utf-8")
    labels = [
        "Enter Your Woodshed", "Create Your Woodchuck",
        "Sign In from New Device", "P-Chart Quick Submit",
    ]
    positions = [html.index(label) for label in labels]
    assert positions == sorted(positions)
    assert 'class="btn welcome-enter-button" href="/home"' in html
    assert 'href="/setup">Create Your Woodchuck' in html
    assert 'href="/login">Sign In from New Device' in html
    assert "Back to the Woodshed" not in html


def test_shed_nameplate_level_team_badge_and_mirrored_secret_hooks() -> None:
    html = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert html.count('id="woodchuck-name-value"') == 1
    assert "shed-readout-name" in html and ".shed-readout-name" in css
    assert 'id="shed-team-button"' in html and 'id="dandelion-object"' not in html
    level_css = css[css.rindex(".shed-readout-level {"):css.index("\n}", css.rindex(".shed-readout-level {"))]
    assert "background: radial-gradient" in level_css
    assert "border: 3px ridge #8b5c1d !important" in level_css
    assert "border-radius: 50%" in level_css
    assert "bottom: calc(4.75rem + env(safe-area-inset-bottom))" in css
    assert "width: 44px" in css and "height: 44px" in css


def test_board_wording_team_cards_placard_and_plunge_accessibility() -> None:
    html = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    assert '>PRACTICE MINUTES LEADERBOARD</h4>' in html
    assert "All contest-enabled P-Charts count toward weekly practice minutes. Verified standings include only charts that have been verified." in html
    assert "Each contest win earns one Camp Point and one dandelion." not in html
    assert 'aria-label="Play Plunge Burrow"' in html and 'title="Play Plunge Burrow"' in html
    assert '<span aria-hidden="true">🕳️</span></a>' in html
    for title in (
        "Board Activity Points this Week by Team",
        "Practice Minutes this Week by Team",
        "Practice Minutes this Week by Team Average",
        "Practice Minutes Lifetime by Team",
        "Team Practice Rating",
    ):
        assert title in html
    assert html.count('class="board-practice-section bonus-challenge-section"') == 1


def test_shop_practice_rooms_preserve_tools_and_add_arcade_door() -> None:
    html = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert '"practice-room": "Practice Rooms"' in script
    assert "Take a private lesson using special tools to help develop your Woodchuck's musical skills." not in html
    assert 'href="https://brassspectrogram.netlify.app/"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert 'aria-label="Open Spectrogram" title="Brass Practice Tool"' in html
    assert 'disabled aria-label="Pristine P-Chart — Coming Soon" title="Pristine P-Chart — Coming Soon"' in html
    assert 'href="/arcade" aria-label="Open Arcade Room"' in html
    assert html.count('class="practice-room-emoji-control practice-room-door"') == 3
    assert "iframe" not in html.casefold() and "microphone" not in html.casefold()


def test_main_templates_still_render() -> None:
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/home").status_code == 200
        assert client.get("/p-book").status_code == 200
        assert client.get("/quest").status_code == 200
        assert client.get("/store").status_code == 200
