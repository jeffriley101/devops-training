from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates/base.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")


def shared_sound_block() -> str:
    start = CSS.index("/* Main pages share a predictable lower launcher")
    return CSS[start:CSS.index("/* Plunge Burrow prototype", start)]


def test_shared_sound_controls_render_on_main_pages_except_shop() -> None:
    client = TestClient(app)
    for path in ("/p-book", "/quest"):
        response = client.get(path)
        assert response.status_code == 200
        assert '<body class="main-app-page' in response.text
        assert 'class="sound-effects-controls"' in response.text

    shop = client.get("/store")
    assert shop.status_code == 200
    assert '<body class="main-app-page' in shop.text
    assert 'class="sound-effects-controls"' not in shop.text

    assert (
        '<body{% if page_class %} class="{{ page_class }}"{% endif %} '
        'data-authenticated="{{ \'true\' if authenticated_profile else \'false\' }}">'
        in BASE
    )
    assert '{% if active_nav not in ("home", "store") %}' in BASE
    assert CSS.count(".main-app-page .sound-effects-controls") == 1
    assert ".woodshed-object-column-right > .shed-sound-effects-controls" in CSS
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

    shed_start = shared.index(".woodshed-object-column-right > .shed-sound-effects-controls {")
    shed_end = shared.index(".woodshed-object-column-right > .shed-sound-effects-controls .sound-effects-button", shed_start)
    shed_controls = shared[shed_start:shed_end]
    assert "position: relative" in shed_controls
    assert "inset: auto" in shed_controls
    shed_button_start = shed_end
    shed_button_end = shared.index(".woodshed-object-column-right > .shed-sound-effects-controls .sound-effects-panel", shed_button_start)
    shed_button = shared[shed_button_start:shed_button_end]
    assert "width: 3.5rem" in shed_button and "height: 3.5rem" in shed_button
    assert "background: transparent" in shed_button
    assert "filter: drop-shadow" in shed_button


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
    right = templates["SHED"][templates["SHED"].index("woodshed-object-column-right"):templates["SHED"].index("id=\"shed-decorate-panel\"")]
    assert right.index('id="shed-decorate-button"') < right.index('id="sound-effects-button"')
    assert "practice-timer" in templates["BOOK"] and "Submit" in templates["BOOK"]
    assert "Bonus Challenge" in templates["BOARD"] and "plunge-burrow-button" in templates["BOARD"]
    assert 'data-shop-panel-content="gear"' in templates["SHOP"]
    assert 'data-shop-panel-content="little-buddy"' in templates["SHOP"]
    assert 'aria-label="Sound Effects On. Open settings."' in BASE
    assert 'aria-controls="sound-effects-panel"' in BASE
    assert ".sound-effects-button:focus-visible" in CSS


def test_mobile_shed_columns_center_controls_without_changing_their_vertical_flow() -> None:
    mobile_start = CSS.index("/* Mobile SHED: full-height left/right control columns */")
    mobile = CSS[mobile_start:CSS.index("/* Mobile SHED fine-tuning */", mobile_start)]
    assert mobile.count("align-items: center") == 2
    assert ".woodshed-object-column-left > *," in mobile
    assert "align-self: center" in mobile
    assert "margin-left: 0" in mobile


def test_staged_mobile_shed_controls_use_shared_centered_lanes() -> None:
    start = CSS.index("/* === TEMP SHED 5X2 CONTROL GRID === */")
    staged = CSS[start:]

    assert ".ww-shed-control-column {" in staged
    assert "width: 3.5rem" in staged
    assert ".ww-shed-control-left {" in staged
    assert "left: 0.65rem !important" in staged
    assert ".ww-shed-control-right {" in staged
    assert "right: 0.65rem !important" in staged

    centered = staged[
        staged.index(".woodshed-foreground.ww-shed-grid .ww-shed-control-column > * {"):
        staged.index("#sound-effects-button {", staged.index(".woodshed-foreground.ww-shed-grid .ww-shed-control-column > * {"))
    ]
    assert "left: 50% !important" in centered
    assert "right: auto !important" in centered
    assert "transform: translate(-50%, -50%) !important" in centered

    # The existing row positions remain the vertical layout source of truth.
    rows = staged[staged.index("/* ROW 1 */"):staged.index(".woodshed-foreground.ww-shed-grid .ww-shed-control-column > * {")]
    for selector, top in (
        ("#woodchuck-name-value", "8%"),
        ("#instrument-object", "28%"),
        ("#xp-level-control", "48%"),
        ("#shed-decorate-button", "68%"),
        ("#mum-open-button", "88%"),
        ("#shed-team-button", "8%"),
        ("#level-value", "28%"),
        ("#metronome-open-button", "48%"),
        ("#tuner-open-button", "68%"),
        (".shed-sound-effects-controls", "88%"),
    ):
        rule_start = rows.index(f"{selector} {{")
        rule = rows[rule_start:rows.index("}", rule_start)]
        assert f"top: {top} !important" in rule


def test_shed_and_shop_interactive_emojis_share_layout_safe_pop_feedback() -> None:
    pop = CSS[CSS.index(".room-object,"):CSS.index(".room-object:focus-visible")]

    for selector in (
        ".room-object",
        ".shed-icon-object",
        ".xp-level-control",
        ".shed-readout-level",
        ".shed-sound-effects-controls .sound-effects-button",
        ".shop-scene-control",
    ):
        assert selector in pop
    assert "transition:" in pop and "scale 150ms ease" in pop
    assert "scale: 1.1" in pop
    assert "scale: 1.16" in pop
    assert "):active:not(:disabled)" in pop
    assert ".shed-decoration" not in pop


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
