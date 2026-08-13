from pathlib import Path

from app.content import LEVEL_OPTIONS


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "templates" / "home.html").read_text()
APP = (ROOT / "static" / "js" / "app.js").read_text()
CSS = (ROOT / "static" / "css" / "styles.css").read_text()
MAIN = (ROOT / "app" / "main.py").read_text()


def xp_javascript() -> str:
    start = APP.index("  function wireXpPanel() {")
    end = APP.index("  function wireShedSecret() {", start)
    return APP[start:end]


def test_xp_control_is_distinct_and_directly_under_instrument() -> None:
    instrument = HOME.index("id=\"instrument-object\"")
    xp_control = HOME.index("id=\"xp-level-control\"")
    profile_level = HOME.index("id=\"level-value\"")

    assert instrument < xp_control < profile_level
    assert "aria-controls=\"xp-panel\"" in HOME[xp_control:profile_level]
    assert HOME.count("id=\"xp-level-control\"") == 1
    assert HOME.count("id=\"level-value\"") == 1


def test_xp_panel_lists_every_lifetime_source() -> None:
    panel_start = HOME.index("id=\"xp-panel\"")
    panel_end = HOME.index("id=\"shed-team-panel\"", panel_start)
    panel = HOME[panel_start:panel_end]

    assert "Practice Minutes" in panel
    assert "Board Points" in panel
    assert "P-Charts" in panel
    assert "Plunge Points" in panel
    assert "id=\"xp-progress\"" in panel
    assert "Max Level" in panel


def test_xp_panel_fetches_calculated_xp_and_handles_max_level() -> None:
    javascript = xp_javascript()

    assert "fetch(\"/xp\"" in javascript
    assert "payload.level === 10 || payload.next_level_xp === null" in javascript
    assert "? `${payload.xp_total} lifetime XP`" in javascript
    assert ": `${payload.xp_total} XP / ${payload.next_level_xp} XP`" in javascript
    assert "maxLevelEl.hidden = !isMaxLevel" in javascript
    assert "XP is unavailable right now." in javascript


def test_profile_skill_level_editor_remains_separate() -> None:
    home_route_start = MAIN.index("@app.get(\"/home\")")
    home_route_end = MAIN.index("@app.get(\"/p-book\")", home_route_start)
    home_route = MAIN[home_route_start:home_route_end]
    assert "levels=LEVEL_OPTIONS" in home_route

    select_start = HOME.index("<select id=\"change-level-select\"")
    select_end = HOME.index("</select>", select_start)
    profile_level_select = HOME[select_start:select_end]
    assert "{% for item in levels %}" in profile_level_select
    assert "<option value=\"{{ item }}\">{{ item }}</option>" in profile_level_select
    assert {"Beginner", "Intermediate", "Advanced"}.issubset(LEVEL_OPTIONS)

    profile_control_start = HOME.index("id=\"level-value\"")
    profile_control_end = HOME.index("</button>", profile_control_start)
    profile_control = HOME[profile_control_start:profile_control_end]
    xp_control_start = HOME.index("id=\"xp-level-control\"")
    xp_control_end = HOME.index("</button>", xp_control_start)
    xp_control = HOME[xp_control_start:xp_control_end]
    assert "aria-controls=\"change-level-panel\"" in profile_control
    assert "aria-controls=\"xp-panel\"" in xp_control
    assert profile_control_start != xp_control_start
    assert "id=\"change-level-panel\"" in HOME

    hydrate_start = APP.index("  function hydrateHome(state) {")
    hydrate_end = APP.index("  function wireXpPanel() {", hydrate_start)
    hydrate = APP[hydrate_start:hydrate_end]
    assert "const levelEl = document.getElementById(\"level-value\");" in hydrate
    assert "const profileLevel = state.profile.level || \"Level not set\";" in hydrate
    assert "levelEl.textContent = profileLevel === \"Level not set\"" in hydrate
    assert "const control = document.getElementById(\"xp-level-control\");" in xp_javascript()


def test_mobile_left_controls_are_compact_and_keep_markup_order() -> None:
    start = CSS.index("/* Compact mobile SHED left controls")
    end = CSS.index("/* SHED lifetime XP badge", start)
    mobile = CSS[start:end]

    assert "@media (max-width: 640px)" in mobile
    assert ".woodshed-object-column-left {" in mobile
    assert "justify-content: flex-start" in mobile
    assert "gap: 0.6rem" in mobile
    assert "height: auto !important" in mobile
    assert ".woodshed-object-column-left > *" in mobile
    assert "position: static !important" in mobile
    assert HOME.index('id="instrument-object"') < HOME.index('id="xp-level-control"') < HOME.index('id="level-value"')
