from pathlib import Path


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
    audio_pos = BASE.index('/static/js/audio.js?v=4')
    app_pos = BASE.index('/static/js/app.js?v=25')
    assert tone_pos < audio_pos < app_pos


def test_audio_unlock_is_gesture_only_lazy_and_reuses_graph():
    assert 'document.addEventListener("pointerdown", gestureUnlock, true)' in AUDIO
    assert 'document.addEventListener("click", gestureUnlock, true)' in AUDIO
    assert 'document.addEventListener("keydown", gestureUnlock, true)' in AUDIO
    assert 'document.removeEventListener("pointerdown", gestureUnlock, true)' in AUDIO
    assert 'document.addEventListener("DOMContentLoaded", wireControls' in AUDIO
    assert "Tone.start()" in AUDIO
    assert "if (graph || !window.Tone) return graph" in AUDIO
    assert "if (unlocked) return Promise.resolve(true)" in AUDIO
    assert ".catch(function () { return false; })" in AUDIO
    assert "Transport" not in AUDIO


def test_effect_preferences_are_local_accessible_and_independent():
    assert 'const DEFAULT_VOLUME = 0.35' in AUDIO
    assert 'readBoolean(STORAGE_ENABLED, true)' in AUDIO
    assert "window.localStorage" in AUDIO
    assert "/account/state" not in AUDIO
    assert "metronome" not in AUDIO.casefold()
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
    assert APP.count('playSound("dialClick")') == 3
    choose = APP.index('chooseQuestBtn.addEventListener("click"')
    skip = APP.index('skipQuestBtn.addEventListener("click"')
    submit = APP.index('form.addEventListener("submit", async function (event)', skip)
    assert choose < APP.index('playSound("dialClick")', choose)
    assert skip < APP.index('playSound("dialClick")', skip)
    assert submit < APP.index('playSound("dialClick")', submit)


def test_visual_confirmation_and_metronome_implementation_remain_present():
    assert "celebrateSuccess" in APP
    assert "success-callout" in APP
    assert "AudioContext" in APP
    metronome = APP[APP.index("function wireMetronome"):APP.index("function wireBandCamp")]
    assert "AudioContext" in metronome
    assert "WoodshedAudio" not in metronome
    assert "Tone" not in metronome
