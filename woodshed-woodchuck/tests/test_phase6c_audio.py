from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates" / "base.html").read_text()
AUDIO = (ROOT / "static" / "js" / "audio.js").read_text()
APP = (ROOT / "static" / "js" / "app.js").read_text()
CSS = (ROOT / "static" / "css" / "styles.css").read_text()
VERSION = (ROOT / "static" / "vendor" / "tone" / "VERSION").read_text()


def test_tone_is_exactly_pinned_local_licensed_and_loaded_in_order():
    tone = ROOT / "static" / "vendor" / "tone" / "Tone.js"
    license_file = ROOT / "static" / "vendor" / "tone" / "LICENSE.md"
    assert tone.is_file() and tone.stat().st_size > 300_000
    assert "tone@15.1.22" in VERSION
    assert "MIT License" in license_file.read_text()
    assert "cdn" not in BASE.casefold()
    tone_pos = BASE.index('/static/vendor/tone/Tone.js?v=15.1.22')
    audio_pos = BASE.index('/static/js/audio.js?v=8')
    app_pos = BASE.index('/static/js/app.js?v=50')
    assert tone_pos < audio_pos < app_pos


def test_audio_unlock_is_gesture_only_lazy_and_reuses_graph():
    assert 'document.addEventListener("pointerdown", gestureUnlock, true)' in AUDIO
    assert 'document.addEventListener("touchend", gestureUnlock, true)' in AUDIO
    assert 'document.addEventListener("click", gestureUnlock, true)' in AUDIO
    assert 'document.addEventListener("keydown", gestureUnlock, true)' in AUDIO
    assert 'document.removeEventListener("pointerdown", gestureUnlock, true)' not in AUDIO
    assert 'document.addEventListener("DOMContentLoaded", wireControls' in AUDIO
    assert "Tone.start()" in AUDIO
    assert "if (graph || !window.Tone) return graph" in AUDIO
    assert "function toneAudioContext()" in AUDIO
    assert "context.rawContext ? context.rawContext : context" in AUDIO
    assert "function audioContextIsRunning()" in AUDIO
    assert 'context.state === "running"' in AUDIO
    assert "function primeAudioContextFromGesture()" in AUDIO
    assert 'context.state !== "running"' in AUDIO
    assert "Promise.resolve(context.resume())" in AUDIO
    assert 'typeof context.createOscillator === "function"' in AUDIO
    assert 'typeof context.createGain === "function"' in AUDIO
    assert "primerGain.gain.setValueAtTime(0.0001, now)" in AUDIO
    assert "source.start(now)" in AUDIO
    assert "source.stop(now + 0.02)" in AUDIO
    assert "primeAudioContextFromGesture();" in AUDIO
    assert AUDIO.index("primeAudioContextFromGesture();") < AUDIO.index(
        "if (unlocked && audioContextIsRunning()) return Promise.resolve(true)"
    )
    assert "if (unlocked && audioContextIsRunning()) return Promise.resolve(true)" in AUDIO
    assert "unlocked = false;" in AUDIO
    assert "function markAudioReady()" in AUDIO
    assert "if (!audioContextIsRunning()) {" in AUDIO
    assert "return markAudioReady();" in AUDIO
    assert "!unlocked || !audioContextIsRunning() || !graph" in AUDIO
    assert "Transport" not in AUDIO


def test_mobile_gestures_resume_a_context_that_suspends_again():
    script = r"""
const fs = require("fs");
const vm = require("vm");
const listeners = {};
let resumeCalls = 0;
let sourceStarts = 0;
const pending = new Promise(function () {});
const rawContext = {
  state: "suspended",
  sampleRate: 44100,
  currentTime: 0,
  destination: {},
  resume: function () { resumeCalls += 1; return pending; },
  createOscillator: function () {
    return {
      connect: function () {},
      disconnect: function () {},
      start: function () { sourceStarts += 1; },
      stop: function () {},
      onended: null,
    };
  },
  createGain: function () {
    return {
      gain: { setValueAtTime: function () {} },
      connect: function () {},
      disconnect: function () {},
    };
  },
};
global.document = {
  addEventListener: function (name, handler) { listeners[name] = handler; },
  getElementById: function () { return null; },
};
global.window = {
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  addEventListener: function (name, handler) { listeners["window:" + name] = handler; },
  Tone: {
    getContext: function () { return { rawContext: rawContext }; },
    start: function () { return pending; },
  },
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
listeners.pointerdown({ type: "pointerdown", isComposing: false });
rawContext.state = "suspended";
listeners.touchend({ type: "touchend", isComposing: false });
if (resumeCalls !== 2 || sourceStarts !== 2) {
  process.exitCode = 1;
}
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "static/js/audio.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_effect_preferences_are_local_accessible_and_independent():
    assert 'const DEFAULT_VOLUME = 0.35' in AUDIO
    assert 'readBoolean(STORAGE_ENABLED, true)' in AUDIO
    assert "window.localStorage" in AUDIO
    assert "/account/state" not in AUDIO
    assert '"#tuner-open-button, #tuner-panel, #metronome-open-button, #metronome-panel"' in AUDIO
    assert "function isDedicatedMediaGesture(event)" in AUDIO
    assert "woodshedWoodchuckMetronomeBpm" not in AUDIO
    assert "Sound Effects" in BASE
    assert 'type="range" min="0" max="100"' in BASE
    assert 'aria-valuetext="35 percent"' in BASE
    assert ":focus-visible" in CSS


def test_all_named_synthesized_effects_route_to_one_master_without_loops():
    effects = {
        "correctTrivia", "incorrectTrivia", "dandelionEarned",
        "campPointEarned", "pChartSubmitted", "crownEarned", "dialClick",
        "secretReward",
    }
    for effect in effects:
        assert f'"{effect}"' in AUDIO
    assert "new Tone.Gain(outputLevel()).toDestination()" in AUDIO
    assert ".connect(master)" in AUDIO
    assert "loop: true" not in AUDIO.casefold()
    assert "crownUntil" in AUDIO
    assert "lastPlayed" in AUDIO


def test_confirmed_action_triggers_and_silent_restoration_paths():
    assert 'if (payload.redeemed)' in APP
    assert 'playSound("secretReward")' in APP
    assert 'if (checkedAnswer.created === true) playSound("incorrectTrivia")' in APP
    assert 'if (checkedAnswer.award_created === true)' in APP
    assert "playCampReward(true)" in APP
    assert 'if (createdPayload.created === true)' in APP
    assert 'playSound("pChartSubmitted")' in APP
    assert APP.count("playCampReward(false)") == 1
    assert 'payload.crown_newly_earned === true' in APP
    hydration = APP[APP.index("async function loadPersistedCampAwards"):APP.index("function setButtonComplete")]
    assert "playSound(" not in hydration
    assert "playCampReward(" not in hydration


def test_quest_dials_click_only_inside_deliberate_handlers():
    assert APP.count('playSound("dialClick")') == 1
    submit = APP.index('form.addEventListener("submit", async function (event)')
    assert submit < APP.index('playSound("dialClick")', submit)


def test_visual_confirmation_and_metronome_implementation_remain_present():
    assert "celebrateSuccess" in APP
    assert "success-callout" in APP
    assert "AudioContext" in APP
    metronome = APP[APP.index("function wireMetronome"):APP.index("function wireBandCamp")]
    assert "AudioContext" in metronome
    assert "WoodshedAudio" not in metronome
    assert "Tone" not in metronome
    assert 'startButton.addEventListener("click", toggleMetronome)' in metronome
    assert 'audioContext.state !== "running"' in metronome
    assert "resumePromise = Promise.resolve(audioContext.resume())" in metronome
    assert "await resumePromise" in metronome
    assert "function primeMetronomeOutput()" in metronome
    assert "gain.gain.setValueAtTime(0.0001, now)" in metronome
    assert metronome.index("primeMetronomeOutput();") < metronome.index(
        "await resumePromise;"
    )
    assert "if (isRunning && contextIsRunning)" in metronome
    assert "gain.connect(audioContext.destination)" in metronome
    assert 'document.addEventListener("visibilitychange"' in metronome
