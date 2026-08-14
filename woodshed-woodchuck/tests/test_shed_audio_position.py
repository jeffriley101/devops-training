from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates/base.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")


def shared_sound_block() -> str:
    start = CSS.index("/* Main pages share a predictable lower launcher")
    return CSS[start:CSS.index("/* Plunge Burrow prototype", start)]


def test_four_main_pages_receive_one_shared_positioning_hook() -> None:
    client = TestClient(app)
    for path in ("/p-book", "/quest", "/store"):
        response = client.get(path)
        assert response.status_code == 200
        assert '<body class="main-app-page' in response.text
        assert 'class="sound-effects-controls"' in response.text

    assert '<body{% if page_class %} class="{{ page_class }}"{% endif %}>' in BASE
    assert CSS.count(".main-app-page .sound-effects-controls") == 1
    assert ".woodshed-scene > .shed-sound-effects-controls" in CSS
    assert ".shop-page .sound-effects-controls" not in CSS
    assert ".board-page .sound-effects-controls" not in CSS


def test_shared_launcher_uses_lower_safe_edge_and_panel_opens_upward() -> None:
    shared = shared_sound_block()
    controls = shared[shared.index(".main-app-page .sound-effects-controls {"):shared.index(".main-app-page .sound-effects-button {")]
    button = shared[shared.index(".main-app-page .sound-effects-button {"):shared.index(".main-app-page .sound-effects-panel {")]
    panel = shared[shared.index(".main-app-page .sound-effects-panel {"):]

    assert "top: auto" in controls
    assert "right: max(0.75rem, env(safe-area-inset-right))" in controls
    assert "bottom: calc(4.75rem + env(safe-area-inset-bottom))" in controls
    assert "width: 2.75rem" in button and "height: 2.75rem" in button
    assert "top: auto" in panel and "bottom: 3.15rem" in panel
    assert "max-height: calc(100vh - 10rem)" in panel
    assert "overflow-y: auto" in panel
    assert "width: min(14rem, calc(100vw - 1.4rem))" in CSS
    assert "overflow-x" not in shared

    shed_start = shared.index(".woodshed-scene > .shed-sound-effects-controls {")
    shed_end = shared.index(".woodshed-scene > .shed-sound-effects-controls .sound-effects-panel", shed_start)
    shed_controls = shared[shed_start:shed_end]
    assert "position: absolute" in shed_controls
    assert "right: max(0.5rem, env(safe-area-inset-right))" in shed_controls
    assert "bottom: max(1.25rem, env(safe-area-inset-bottom))" in shed_controls


def test_shared_placement_keeps_page_content_and_accessibility_intact() -> None:
    templates = {
        "SHED": (ROOT / "templates/home.html").read_text(encoding="utf-8"),
        "BOOK": (ROOT / "templates/p_book.html").read_text(encoding="utf-8"),
        "BOARD": (ROOT / "templates/quest.html").read_text(encoding="utf-8"),
        "SHOP": (ROOT / "templates/store.html").read_text(encoding="utf-8"),
    }
    assert 'id="streak-value"' not in templates["SHED"]
    assert 'id="total-p-charts-value"' not in templates["SHED"]
    assert 'id="p-book"' not in templates["SHED"]
    assert 'aria-label="Open Bulletin Board"' not in templates["SHED"]
    assert 'class="sound-effects-controls shed-sound-effects-controls"' in templates["SHED"]
    assert "practice-timer" in templates["BOOK"] and "Submit" in templates["BOOK"]
    assert "Bonus Challenge" in templates["BOARD"] and "plunge-burrow-button" in templates["BOARD"]
    assert 'data-shop-panel-content="gear"' in templates["SHOP"]
    assert 'data-shop-panel-content="little-buddy"' in templates["SHOP"]
    assert 'aria-label="Sound Effects On. Open settings."' in BASE
    assert 'aria-controls="sound-effects-panel"' in BASE
    assert ".sound-effects-button:focus-visible" in CSS


def test_utility_pages_do_not_receive_main_page_positioning() -> None:
    client = TestClient(app)
    for path in ("/", "/login", "/setup", "/plunge-burrow"):
        response = client.get(path)
        assert response.status_code == 200
        assert '<body class="main-app-page' not in response.text
    assert "main-app-page" not in (ROOT / "templates/contest_admin.html").read_text(encoding="utf-8")
    main_source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    trusted = main_source[main_source.index("def trusted_verifiers_page"):main_source.index("@app.get(\"/setup\")")]
    assert 'page_class="main-app-page"' in trusted  # Preserves its pre-existing lower placement.
