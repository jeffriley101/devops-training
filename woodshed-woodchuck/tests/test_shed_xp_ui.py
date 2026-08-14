from pathlib import Path

from app.content import LEVEL_OPTIONS


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "templates" / "home.html").read_text()
APP = (ROOT / "static" / "js" / "app.js").read_text()
CSS = (ROOT / "static" / "css" / "styles.css").read_text()
MAIN = (ROOT / "app" / "main.py").read_text()


def xp_javascript() -> str:
    start = APP.index("  function wireXpPanel() {")
    end = APP.index("  function wireShedDecorations() {", start)
    return APP[start:end]


def test_xp_control_and_team_are_ordered_in_the_left_column() -> None:
    left_column = HOME.index("woodshed-object-column-left")
    center_column = HOME.index("woodshed-object-column-center", left_column)
    instrument = HOME.index("id=\"instrument-object\"", left_column)
    xp_control = HOME.index("id=\"xp-level-control\"", instrument)
    team = HOME.index("id=\"shed-team-button\"", xp_control)
    profile_level = HOME.index("id=\"level-value\"", team)

    assert left_column < instrument < xp_control < team < profile_level < center_column
    assert "aria-controls=\"xp-panel\"" in HOME[xp_control:team]
    assert HOME.count("id=\"xp-level-control\"") == 1
    assert HOME.count("id=\"level-value\"") == 1
    xp_markup = HOME[xp_control:team]
    assert '<span class="xp-level-symbol" aria-hidden="true">⭐</span>' in xp_markup
    assert 'id="xp-level-number" class="xp-level-number"' in xp_markup


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


def test_mobile_left_and_right_controls_use_intentional_rows() -> None:
    start = CSS.index("/* Keep mobile SHED controls")
    end = CSS.index("/* SHED lifetime XP badge", start)
    mobile = CSS[start:end]

    for selector, position in (
        ("#instrument-object", "12%"),
        ("#xp-level-control", "34%"),
        (".woodshed-object-column-left .dandelion-object", "55%"),
        ("#level-value", "76%"),
        (".metronome-object", "10%"),
        (".tuner-object", "29%"),
        (".chair-object", "48%"),
        (".shed-decorate-button", "67%"),
        (".woodshed-object-column-right > .shed-sound-effects-controls", "86%"),
    ):
        rule_start = mobile.index(selector)
        assert f"top: {position} !important" in mobile[rule_start:rule_start + 220]
    assert "left: 0" in mobile
    assert "right: 0" in mobile

def test_xp_level_uses_an_emoji_token_without_the_old_coin_treatment() -> None:
    badge = CSS[CSS.index(".xp-level-control {"):CSS.index(".xp-panel {")]
    assert "background: transparent" in badge
    assert "border: 0" in badge
    assert "drop-shadow" in badge
    assert ".xp-level-symbol" in badge
    assert ".xp-level-number" in badge
    assert "radial-gradient" not in badge


def test_profile_level_uses_the_gold_medallion_separately_from_xp() -> None:
    start = CSS.rindex(".shed-readout-level {")
    profile_badge = CSS[start:CSS.index(".board-page", start)]
    assert "radial-gradient(circle at 35% 28%" in profile_badge
    assert "border: 3px ridge #8b5c1d" in profile_badge
    assert "border-radius: 50%" in profile_badge
    control_start = HOME.index('id="level-value"')
    control = HOME[control_start:HOME.index("</button>", control_start)]
    assert 'aria-controls="change-level-panel"' in control
