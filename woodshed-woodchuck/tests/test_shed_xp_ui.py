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
    from test_r4a_artwork import cells, SHED
    controls = cells(HOME)
    assert {a['data-scene-cell']: a['id'] for _, a in controls} == SHED
    assert HOME.count('id="xp-level-control"') == 1
    assert HOME.count('id="level-value"') == 1
    assert 'id="xp-level-number" hidden' in HOME
    assert '⭐' not in HOME[:HOME.index('id="sound-effects-panel"')]
    assert '🏅' not in HOME


def test_xp_panel_lists_every_lifetime_source() -> None:
    panel_start = HOME.index("id=\"xp-panel\"")
    panel_end = HOME.index("id=\"shed-team-panel\"", panel_start)
    panel = HOME[panel_start:panel_end]

    assert "Credited Practice Time" in panel
    assert "Board Points" in panel
    assert "P-Charts" in panel
    assert "Plunge Points" in panel
    assert "id=\"xp-progress\"" in panel
    assert "Max Level" in panel


def test_xp_panel_fetches_calculated_xp_and_handles_max_level() -> None:
    javascript = xp_javascript()

    assert "fetch(\"/xp\"" in javascript
    assert "payload.level === 10 || payload.next_level_xp === null" in javascript
    assert "? `${Number(payload.xp_total.toFixed(2))} lifetime XP`" in javascript
    assert ": `${Number(payload.xp_total.toFixed(2))} XP / ${payload.next_level_xp} XP`" in javascript
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


def test_mobile_shed_controls_use_static_five_row_columns() -> None:
    layout = (ROOT / 'static/css/scene-hotspots.css').read_text()
    assert 'grid-template-rows: repeat(5, 20%)' in layout
    assert 'grid-template-columns: repeat(2, 50%)' in layout
    assert 'gap: 0' in layout
    assert 'stageShedGrid' not in APP
    assert 'forceShedPositions' not in APP


def test_xp_cell_uses_artwork_with_an_accessible_label() -> None:
    start = HOME.index('id="xp-level-control"')
    button = HOME[start:HOME.index('</button>', start)]
    assert 'data-scene-cell="L3"' in button
    assert 'aria-label="Open XP and Daily Login Streak"' in button
    assert '⭐' not in button


def test_profile_level_cell_remains_separate_from_xp() -> None:
    control_start = HOME.index('id="level-value"')
    control = HOME[control_start:HOME.index("</button>", control_start)]
    assert 'aria-controls="change-level-panel"' in control
    assert 'data-scene-cell="R2"' in control
    assert "🏅" not in control


def test_shed_team_control_uses_the_configured_emblem_with_the_safe_fallback() -> None:
    button_start = HOME.index('id="shed-team-button"')
    button = HOME[button_start:HOME.index("</button>", button_start)]
    team_wiring_start = APP.index("function wireShedTeamBadge")
    team_wiring_end = APP.index("function wireLoginStreak", team_wiring_start)
    team_wiring = APP[team_wiring_start:team_wiring_end]

    assert 'id="shed-team-emblem"' in button
    assert "renderTeamEmblem(emblem, current?.emblem || \"\")" in team_wiring
    assert "No team" in team_wiring
