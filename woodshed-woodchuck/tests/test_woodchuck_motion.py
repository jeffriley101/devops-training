from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates" / "base.html").read_text()
HOME = (ROOT / "templates" / "home.html").read_text()
MOTION_CSS = (ROOT / "static" / "css" / "woodchuck-motion.css").read_text()
MOTION_JS = (ROOT / "static" / "js" / "woodchuck-motion.js").read_text()


def test_motion_assets_are_loaded_after_the_main_stylesheet():
    assert "/static/css/woodchuck-motion.css" in BASE
    assert BASE.index("/static/css/styles.css") < BASE.index(
        "/static/css/woodchuck-motion.css"
    )
    assert "/static/js/woodchuck-motion.js" in BASE
    assert BASE.index("/static/js/character-reaction.js") < BASE.index(
        "/static/js/woodchuck-motion.js"
    )


def test_motion_layer_preserves_the_server_selected_standalone_character():
    assert 'class="woodshed-character-layer"' in HOME
    assert 'class="character-art woodshed-character-art"' in HOME
    assert 'src="{{ shed_character_url }}"' in HOME
    assert 'src="{{ shed_artwork_url }}"' not in HOME

    # Motion decorates the existing element; it must not replace the selected art.
    assert "art.src =" not in MOTION_JS
    assert "woodchuck-home.png" not in MOTION_JS
    assert (ROOT / "static" / "img" / "woodchuck-saxophone.png").exists()
    assert (ROOT / "static" / "img" / "woodchuck-trumpet.png").exists()
    assert (ROOT / "static" / "img" / "woodchuck-percussion.png").exists()


def test_first_pass_includes_idle_wiggle_and_achievement_motion():
    assert "@keyframes ww-woodchuck-idle" in MOTION_CSS
    assert "@keyframes ww-woodchuck-practice-sway" in MOTION_CSS
    assert "@keyframes ww-woodchuck-tap-wiggle" in MOTION_CSS
    assert "@keyframes ww-woodchuck-achievement-hop" in MOTION_CSS
    assert 'layer.addEventListener("click", wiggle)' in MOTION_JS
    assert 'root.addEventListener("woodshed:celebrate"' in MOTION_JS
    assert 'event.key !== "Enter"' in MOTION_JS


def test_idle_breathing_keeps_the_character_feet_planted():
    art_rule = MOTION_CSS.split(
        ".woodshed-character-layer.ww-motion-ready .woodshed-character-art {", 1
    )[1].split("}", 1)[0]
    idle = MOTION_CSS.split("@keyframes ww-woodchuck-idle {", 1)[1].split(
        "@keyframes", 1
    )[0]
    practice = MOTION_CSS.split(
        "@keyframes ww-woodchuck-practice-sway {", 1
    )[1].split("@keyframes", 1)[0]

    assert "transform-origin: 50% 88%" in art_rule
    assert "animation: ww-woodchuck-idle" in art_rule
    assert "scale(1.016, 1)" in idle
    assert "translateY" not in idle
    assert "translateX(-8px) rotate(-1deg) scale(1, 1)" in practice
    assert "translateX(8px) rotate(1deg) scale(1, 1)" in practice
    assert "translateY" not in practice


def test_metronome_state_uses_practice_sway_only():
    app = (ROOT / "static" / "js" / "app.js").read_text()
    metronome = app[app.index("function wireMetronome"):app.index(
        "const BACK_TO_SCHOOL_READINESS_CHALLENGES"
    )]

    assert 'new CustomEvent("woodshed:practice-sway"' in metronome
    assert "setPracticeSway(true);" in metronome
    assert metronome.count("setPracticeSway(false);") == 2
    assert "if (isRunning) {\n        stopMetronome();" in metronome
    assert "contextIsRunning" not in metronome
    open_handler = metronome[metronome.index(
        'openButton.addEventListener("click"'
    ):metronome.index("if (closeButton)")]
    assert "setPracticeSway" not in open_handler
    close_handler = metronome[metronome.index(
        'closeButton.addEventListener("click"'
    ):metronome.index('startButton.addEventListener("click"')]
    assert "stopMetronome();" in close_handler
    assert 'root.addEventListener("woodshed:practice-sway"' in MOTION_JS
    assert "is-practice-sway" in MOTION_JS
    assert "is-practice-sway" in MOTION_CSS


def test_exactly_nine_non_metronome_shed_controls_trigger_success():
    expected = {
        "woodchuck-name-value",
        "instrument-object",
        "xp-level-control",
        "shed-decorate-button",
        "mum-open-button",
        "shed-team-button",
        "level-value",
        "tuner-open-button",
        "sound-effects-button",
    }
    success_ids = MOTION_JS.split("const SUCCESS_CONTROL_IDS", 1)[1].split(
        "]);", 1
    )[0]

    assert all(f'"{control_id}"' in success_ids for control_id in expected)
    assert success_ids.count('"') == len(expected) * 2
    assert '"metronome-open-button"' not in success_ids
    assert 'control.addEventListener("click", hop)' in MOTION_JS


def test_motion_respects_reduced_motion_and_decorating_mode():
    assert "@media (prefers-reduced-motion: reduce)" in MOTION_CSS
    assert (
        ".woodshed-scene.is-decorating .woodshed-character-layer.ww-motion-ready"
        in MOTION_CSS
    )
    assert "pointer-events: none" in MOTION_CSS


def test_ready_state_makes_character_visible_without_covering_controls():
    ready_rule = MOTION_CSS.split(
        ".woodshed-character-layer.ww-motion-ready {", 1
    )[1].split("}", 1)[0]
    assert "display: block" in ready_rule
    assert "z-index: 4" in ready_rule


def test_motion_script_runs_without_replacing_character_source():
    script = r'''
      class ClassList {
        constructor() { this.values = new Set(); this.addCounts = {}; }
        add(...names) {
          names.forEach((name) => {
            this.values.add(name);
            this.addCounts[name] = (this.addCounts[name] || 0) + 1;
          });
        }
        remove(...names) { names.forEach((name) => this.values.delete(name)); }
        contains(name) { return this.values.has(name); }
      }

      function eventTarget(extra = {}) {
        const listeners = {};
        return Object.assign({
          listeners,
          addEventListener(type, callback) { listeners[type] = callback; },
        }, extra);
      }

      const art = eventTarget({
        classList: new ClassList(),
        offsetWidth: 240,
        src: "/static/img/woodchuck-trumpet.png",
      });
      const attributes = {};
      const controlIds = [
        "woodchuck-name-value",
        "instrument-object",
        "xp-level-control",
        "shed-decorate-button",
        "mum-open-button",
        "shed-team-button",
        "level-value",
        "metronome-open-button",
        "tuner-open-button",
        "sound-effects-button",
      ];
      const controls = Object.fromEntries(controlIds.map((id) => [id, eventTarget()]));
      const layer = eventTarget({
        classList: new ClassList(),
        querySelector(selector) {
          return selector === ".woodshed-character-art" ? art : null;
        },
        setAttribute(name, value) { attributes[name] = value; },
      });
      const windowListeners = {};
      global.window = {
        document: {
          readyState: "complete",
          querySelector(selector) {
            return selector === ".woodshed-character-layer" ? layer : null;
          },
          getElementById(id) { return controls[id] || null; },
        },
        addEventListener(type, callback) { windowListeners[type] = callback; },
      };

      require("./static/js/woodchuck-motion.js");

      if (!layer.classList.contains("ww-motion-ready")) throw new Error("not ready");
      if (attributes.role !== "button" || attributes.tabindex !== "0") {
        throw new Error("character layer is not keyboard operable");
      }
      layer.listeners.click();
      if (!art.classList.contains("is-tap-wiggle")) throw new Error("tap failed");
      art.listeners.animationend({ animationName: "ww-woodchuck-tap-wiggle" });
      if (art.classList.contains("is-tap-wiggle")) throw new Error("tap did not reset");
      windowListeners["woodshed:practice-sway"]({ detail: { active: true } });
      if (!art.classList.contains("is-practice-sway")) throw new Error("sway failed");
      if (controls["metronome-open-button"].listeners.click) {
        throw new Error("metronome was wired to hop");
      }
      for (const id of controlIds.filter((id) => id !== "metronome-open-button")) {
        const before = art.classList.addCounts["is-achievement-hop"] || 0;
        controls[id].listeners.click();
        const after = art.classList.addCounts["is-achievement-hop"] || 0;
        if (after !== before + 1) throw new Error(`${id} did not hop exactly once`);
        art.listeners.animationend({ animationName: "ww-woodchuck-achievement-hop" });
      }
      if (!art.classList.contains("is-practice-sway")) {
        throw new Error("sway did not resume after hop");
      }
      windowListeners["woodshed:practice-sway"]({ detail: { active: false } });
      if (art.classList.contains("is-practice-sway")) throw new Error("sway did not stop");
      windowListeners["woodshed:celebrate"]();
      if (!art.classList.contains("is-achievement-hop")) throw new Error("success failed");
      if (art.src !== "/static/img/woodchuck-trumpet.png") {
        throw new Error("character source changed");
      }
    '''
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
