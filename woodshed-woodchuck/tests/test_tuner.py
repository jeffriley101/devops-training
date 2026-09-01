import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "templates/home.html").read_text(encoding="utf-8")
BASE = (ROOT / "templates/base.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
APP = (ROOT / "static/js/app.js").read_text(encoding="utf-8")


def run_tuner_javascript(source: str):
    script = 'require("./static/js/tuner.js");\n' + source
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_tuning_classification_boundaries():
    actual = run_tuner_javascript(
        """
const cents = [0, -0.5, 0.5, -0.5001, 0.5001, -3, 3, -3.0001, 3.0001, -18, 18, -18.0001, 18.0001];
console.log(JSON.stringify(cents.map((value) => WWTuner.classifyCents(value))));
"""
    )
    assert actual == [
        "PRISTINE",
        "PRISTINE",
        "PRISTINE",
        "GOOD",
        "GOOD",
        "GOOD",
        "GOOD",
        "FLAT",
        "SHARP",
        "FLAT",
        "SHARP",
        "VERY FLAT",
        "VERY SHARP",
    ]


def test_pitch_detector_maps_a_sustained_a4_and_rejects_silence():
    result = run_tuner_javascript(
        """
const sampleRate = 48000;
const samples = new Float32Array(4096);
for (let index = 0; index < samples.length; index += 1) {
  samples[index] = 0.5 * Math.sin(2 * Math.PI * 440 * index / sampleRate);
}
const frequency = WWTuner.detectPitch(samples, sampleRate);
const silence = WWTuner.detectPitch(new Float32Array(4096), sampleRate);
console.log(JSON.stringify({ frequency, note: WWTuner.frequencyToNote(frequency), silence }));
"""
    )
    assert abs(result["frequency"] - 440) < 0.5
    assert result["note"]["name"] == "A4"
    assert abs(result["note"]["cents"]) < 0.5
    assert result["silence"] is None


def test_tuner_is_full_screen_minimal_and_uses_locked_state_colors():
    assert 'id="tuner-panel"' in HOME
    assert 'role="dialog"' in HOME
    assert 'aria-modal="true"' in HOME
    assert 'id="tuner-note"' in HOME
    assert 'id="tuner-diagnosis"' in HOME
    assert 'id="tuner-close-button"' in HOME
    assert "STOP" in HOME
    tuner_markup = HOME[HOME.index('id="tuner-panel"'):HOME.index('id="mum-panel"')]
    for forbidden in ("Hz", "cents", "spectrum", "needle", "gauge"):
        assert forbidden.casefold() not in tuner_markup.casefold()
    tuner_css = CSS[CSS.index(".tuner-panel {"):CSS.index(".chair-object {")]
    assert "position: fixed" in tuner_css
    assert ".tuner-state-flat,\n.tuner-state-sharp" in CSS
    assert ".tuner-state-very-flat,\n.tuner-state-very-sharp" in CSS
    assert "/static/js/tuner.js?v=1" in BASE
    assert BASE.index("/static/js/tuner.js?v=1") < BASE.index("/static/js/app.js?v=78")


def test_tuner_requests_microphone_smooths_results_and_releases_resources():
    tuner_code = APP[APP.index("  function wireTuner() {"):APP.index("  function launchDigitalRain")]
    assert "navigator.mediaDevices.getUserMedia" in tuner_code
    assert "createMediaStreamSource" in tuner_code
    assert "createAnalyser" in tuner_code
    assert "getFloatTimeDomainData" in tuner_code
    assert "frequencyHistory.length > 5" in tuner_code
    assert "candidateFrames < 2" in tuner_code
    assert "mediaSource.connect(analyser)" in tuner_code
    assert "connect(audioContext.destination)" not in tuner_code
    assert "window.cancelAnimationFrame(animationFrame)" in tuner_code
    assert "mediaStream.getTracks().forEach(function (track) { track.stop(); })" in tuner_code
    assert "audioContext.close()" in tuner_code
    assert 'event.key === "Escape"' in tuner_code
    assert 'window.addEventListener("pagehide"' in tuner_code
    assert "requestedStream.getTracks().forEach(function (track) { track.stop(); })" in tuner_code


def test_tuner_open_close_state_and_permission_failure_are_graceful():
    tuner_code = APP[APP.index("  function wireTuner() {"):APP.index("  function launchDigitalRain")]
    assert "panel.hidden = false" in tuner_code
    assert "panel.hidden = true" in tuner_code
    assert 'openButton.setAttribute("aria-expanded", "true")' in tuner_code
    assert 'openButton.setAttribute("aria-expanded", "false")' in tuner_code
    assert ">READY<" in HOME
    assert ">LISTENING<" not in HOME
    assert 'renderNeutral("REQUESTING MICROPHONE")' in tuner_code
    assert 'renderNeutral("LISTENING")' in tuner_code
    assert 'return "MICROPHONE DENIED"' in tuner_code
    assert 'renderNeutral("SECURE CONNECTION REQUIRED")' in tuner_code
    assert 'renderNeutral("MICROPHONE UNSUPPORTED")' in tuner_code
    assert 'return track.readyState === "live"' in tuner_code
    assert 'microphoneTrack.readyState !== "live"' in tuner_code
    get_user_media_call = (
        "requestedStreamPromise = navigator.mediaDevices.getUserMedia"
    )
    assert tuner_code.index("new AudioContextClass()") < tuner_code.index(
        get_user_media_call
    )
    assert tuner_code.index(get_user_media_call) < tuner_code.index(
        "await requestedStreamPromise"
    )
    assert tuner_code.index("await requestedStreamPromise") < tuner_code.index(
        "microphoneTrack = liveTrack"
    )
    assert tuner_code.index("microphoneTrack = liveTrack") < tuner_code.index(
        "createMediaStreamSource"
    )
    media_source_at = tuner_code.index("createMediaStreamSource")
    assert 'renderNeutral("LISTENING")' in tuner_code[media_source_at:]
    assert "if (currentSession !== sessionNumber || panel.hidden)" in tuner_code
