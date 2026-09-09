from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates/base.html").read_text(encoding="utf-8")
HOME = (ROOT / "templates/home.html").read_text(encoding="utf-8")
MOTION_CSS = (ROOT / "static/css/woodchuck-motion.css").read_text(encoding="utf-8")
MOTION_JS = (ROOT / "static/js/woodchuck-motion.js").read_text(encoding="utf-8")


def test_motion_assets_are_loaded_after_the_main_stylesheet() -> None:
    assert '/static/css/woodchuck-motion.css?v=1' in BASE
    assert BASE.index('/static/css/styles.css?v=118') < BASE.index(
        '/static/css/woodchuck-motion.css?v=1'
    )
    assert '/static/js/woodchuck-motion.js?v=1' in BASE


def test_motion_layer_reuses_the_existing_standalone_woodchuck_art() -> None:
    assert 'class="woodshed-character-layer"' in HOME
    assert 'class="character-art woodshed-character-art"' in HOME
    assert 'const WOODCHUCK_MOTION_IMAGE = "/static/img/woodchuck-home.png";' in MOTION_JS
    assert (ROOT / "static/img/woodchuck-home.png").is_file()


def test_first_pass_includes_idle_wiggle_and_achievement_motion() -> None:
    assert "@keyframes ww-woodchuck-idle" in MOTION_CSS
    assert "@keyframes ww-woodchuck-tap-wiggle" in MOTION_CSS
    assert "@keyframes ww-woodchuck-achievement-hop" in MOTION_CSS
    assert 'layer.addEventListener("click", wiggle)' in MOTION_JS
    assert 'root.addEventListener("woodshed:celebrate"' in MOTION_JS
    assert 'event.key !== "Enter" && event.key !== " "' in MOTION_JS


def test_motion_respects_reduced_motion_and_does_not_capture_decorating() -> None:
    assert "@media (prefers-reduced-motion: reduce)" in MOTION_CSS
    assert ".woodshed-scene.is-decorating .woodshed-character-layer.ww-motion-ready" in MOTION_CSS
    assert "pointer-events: none;" in MOTION_CSS
