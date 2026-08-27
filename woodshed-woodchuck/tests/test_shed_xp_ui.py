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


def test_shed_controls_use_requested_left_and_right_columns() -> None:
    left_column = HOME.index("woodshed-object-column-left")
    center_column = HOME.index("woodshed-object-column-center", left_column)
    instrument = HOME.index("id=\"instrument-object\"", left_column)
    team = HOME.index("id=\"shed-team-button\"", instrument)
    stickerbook = HOME.index("id=\"shed-decorate-button\"", team)
    mum = HOME.index("id=\"mum-open-button\"", stickerbook)
    right_column = HOME.index("woodshed-object-column-right", center_column)
    xp_control = HOME.index("id=\"xp-level-control\"", right_column)
    profile_level = HOME.index("id=\"level-value\"", xp_control)
    metronome = HOME.index("id=\"metronome-open-button\"", profile_level)
    tuner = HOME.index("id=\"tuner-open-button\"", metronome)
    audio = HOME.index("id=\"sound-effects-button\"", tuner)

    assert left_column < instrument < team < stickerbook < mum < center_column < right_column
    assert right_column < xp_control < profile_level < metronome < tuner < audio
    assert "aria-controls=\"xp-panel\"" in HOME[xp_control:profile_level]
    assert HOME.count("id=\"xp-level-control\"") == 1
    assert HOME.count("id=\"level-value\"") == 1
    xp_markup = HOME[xp_control:profile_level]
    assert '<span class="xp-level-symbol" aria-hidden="true">⭐</span>' in xp_markup
    assert 'id="xp-level-number" class="sr-only"' in xp_markup
    level_markup = HOME[profile_level:metronome]
    assert '<span class="room-object-icon" aria-hidden="true">🏅</span>' in level_markup


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
    assert "levelEl.textContent" not in hydrate
    assert "`Level: ${profileLevel}. Change level.`" in hydrate
    assert "const control = document.getElementById(\"xp-level-control\");" in xp_javascript()


def test_mobile_shed_controls_keep_requested_rows_after_side_swap() -> None:
    stage_start = APP.index("function stageShedGrid")
    stage_end = APP.index("function stageShopDandelion", stage_start)
    stage = APP[stage_start:stage_end]
    assert stage.index('"#shed-team-button"') < stage.index('"#shed-decorate-button"')
    assert stage.index('"#xp-level-control"') < stage.index('"#level-value"')
    assert '"grid-template-rows", "repeat(5, 1fr)"' in APP

def test_xp_level_uses_an_emoji_token_without_the_old_coin_treatment() -> None:
    badge = CSS[CSS.index(".xp-level-control {"):CSS.index(".xp-panel {")]
    assert "background: transparent" in badge
    assert "border: 0" in badge
    assert "drop-shadow" in badge
    assert ".xp-level-symbol" in badge
    assert "radial-gradient" not in badge


def test_profile_level_uses_the_gold_medal_emoji_separately_from_xp() -> None:
    control_start = HOME.index('id="level-value"')
    control = HOME[control_start:HOME.index("</button>", control_start)]
    assert 'aria-controls="change-level-panel"' in control
    assert "🏅" in control


def test_shed_team_control_uses_the_configured_emblem_with_the_safe_fallback() -> None:
    button_start = HOME.index('id="shed-team-button"')
    button = HOME[button_start:HOME.index("</button>", button_start)]
    team_wiring_start = APP.index("function wireShedTeamBadge")
    team_wiring_end = APP.index("function wireLoginStreak", team_wiring_start)
    team_wiring = APP[team_wiring_start:team_wiring_end]

    assert 'id="shed-team-emblem"' in button
    assert "renderTeamEmblem(emblem, current?.emblem || \"\")" in team_wiring
    assert "No team" in team_wiring
