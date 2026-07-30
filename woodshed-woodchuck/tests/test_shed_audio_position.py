from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates/base.html").read_text()
CSS = (ROOT / "static/css/styles.css").read_text()


def test_only_shed_receives_the_sound_positioning_hook() -> None:
    assert '<body{% if active_nav == "home" %} class="shed-screen"{% endif %}>' in BASE
    assert 'class="sound-effects-controls"' in BASE
    assert 'aria-label="Sound Effects On. Open settings."' in BASE
    assert CSS.count(".shed-screen .sound-effects-controls") == 2
    assert ".board-page .sound-effects-controls" not in CSS
    assert ".shop-page .sound-effects-controls" not in CSS


def test_shed_button_uses_lower_safe_edge_and_panel_opens_upward() -> None:
    start = CSS.index("/* SHED keeps Sound Effects")
    shed = CSS[start:]
    controls = shed[shed.index(".shed-screen .sound-effects-controls {"):shed.index(".shed-screen .sound-effects-button {")]
    panel = shed[shed.index(".shed-screen .sound-effects-panel {"):shed.index("@media (max-width: 640px)")]
    assert "top: auto" in controls
    assert "bottom: calc(4.75rem + env(safe-area-inset-bottom))" in controls
    assert "right: max(0.75rem, env(safe-area-inset-right))" in controls
    assert "top: auto" in panel and "bottom: 3.15rem" in panel
    assert "width: min(14rem, calc(100vw - 1.4rem))" in CSS


def test_shed_mobile_avoids_top_right_and_retains_44px_target() -> None:
    start = CSS.index("/* SHED keeps Sound Effects")
    shed = CSS[start:]
    mobile_start = shed.index("@media (max-width: 640px)")
    mobile = shed[mobile_start:shed.index("\n}", mobile_start) + 2]
    assert "top:" not in mobile
    assert "bottom: calc(4.5rem + env(safe-area-inset-bottom))" in mobile
    assert "right: max(0.65rem, env(safe-area-inset-right))" in mobile
    button = shed[shed.index(".shed-screen .sound-effects-button {"):shed.index(".shed-screen .sound-effects-panel {")]
    assert "width: 2.75rem" in button
    assert "height: 2.75rem" in button
    assert "overflow-x" not in shed
