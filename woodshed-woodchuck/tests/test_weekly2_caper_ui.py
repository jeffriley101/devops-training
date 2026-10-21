"""Source integration invariants; runtime gate/read-only checks live in browsers."""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_default_off_gate_and_conditional_assets():
    gate=(ROOT/'app/feature_access.py').read_text()
    assert '"weekly_plans": Feature(False, "open")' in gate
    main=(ROOT/'app/main.py').read_text()
    assert 'weekly_plans_allowed = can_use_feature(session, profile.id, "weekly_plans")' in main
    for file,marker in [('templates/base.html','weekly-caper.js'),('templates/home.html','weekly-caper-open')]:
        source=(ROOT/file).read_text()
        assert '{% if weekly_plans_allowed %}' in source and marker in source


def test_controller_has_no_state_wallet_or_unrelated_service_writer():
    source=(ROOT/'static/js/weekly-caper.js').read_text()
    for forbidden in ['saveState(', 'applyEconomy(', 'localStorage.clear(', 'innerHTML',
                      '/weekly/wheel', '/weekly/pantry', '/newsletter', 'WWState.set']:
        assert forbidden not in source
    assert 'stateForResponse(account)' in source
    assert "window.addEventListener('ww:session-changed'" in source
    assert "window.addEventListener('pagehide'" in source
    assert "navigator.locks.request(storageKey" in source


def test_existing_surface_care_and_accessible_fallbacks():
    shared=(ROOT/'static/js/shared-ui.js').read_text()
    assert "'mum-panel': 'mum-close-button'" in shared
    assert 'node.inert = active' in shared
    app=(ROOT/'static/js/app.js').read_text().split('function wireMum(state)')[1].split('function wireMetronome()')[0]
    for marker in ['woodshed:caper-opened','woodshed:mum-closed','window.clearTimeout(mumReadyTimer)','openButton.focus()']:
        assert marker in app
    css=(ROOT/'static/css/weekly-caper.css').read_text()
    assert 'min-height: 44px' in css and 'prefers-reduced-motion' in css
    assert '#mum-panel [hidden]' in css
    template=(ROOT/'templates/_weekly_caper.html').read_text()
    assert 'aria-live="polite"' in template and 'role="status"' in template


def test_catalog_matches_sealed_backend_without_extra_tasks():
    source=(ROOT/'static/js/weekly-caper.js').read_text()
    from app.content import GOAL_OPTIONS
    for focus in GOAL_OPTIONS:assert focus in source
    from app.weekly_caper import LANES
    assert LANES==('logging','practice_minutes','homework','board','thirds')
    for lane in LANES:assert "'"+lane+"'" in source
    ast.parse((ROOT/'app/main.py').read_text())
