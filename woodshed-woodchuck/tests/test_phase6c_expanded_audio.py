from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIO = (ROOT / "static/js/audio.js").read_text()
APP = (ROOT / "static/js/app.js").read_text()
STORE = (ROOT / "templates/store.html").read_text()
GOAT_DIR = ROOT / "static/audio/goats"


def test_six_expanded_effects_and_original_eight_are_registered():
    original = {
        "correctTrivia", "incorrectTrivia", "dandelionEarned",
        "campPointEarned", "pChartSubmitted", "crownEarned", "dialClick",
        "secretReward",
    }
    expanded = {
        "goatTracker", "questCompleted", "bandCampBonus",
        "marchingCompleted", "practiceRoomOpen", "medalEarned",
    }
    for effect in original | expanded:
        assert f'"{effect}"' in AUDIO
    assert "Transport" not in AUDIO
    assert "background music" not in AUDIO.casefold()


def test_goat_pool_is_local_nonrepeating_nonoverlapping_and_failure_safe():
    expected = [
        "/static/audio/goats/goat-01.mp3",
        "/static/audio/goats/goat-02.mp3",
        "/static/audio/goats/goat-03.mp3",
        "/static/audio/goats/goat-04.mp3",
        "/static/audio/goats/goat-05.mp3",
    ]
    for url in expected:
        assert f'"{url}"' in AUDIO
    assert "new Tone.Player" in AUDIO
    assert "loop: false" in AUDIO
    assert "GOAT_CLIP_URLS.filter(isLocalGoatUrl)" in AUDIO
    assert "Tone.getContext().decodeAudioData" in AUDIO
    assert "Promise.all(loads)" in AUDIO
    assert ".catch(function () {})" in AUDIO
    assert "new Tone.Gain(0.35).connect(master)" in AUDIO
    assert "if (length > 1 && index === lastGoatIndex)" in AUDIO
    assert "if (player.state === \"started\") player.stop()" in AUDIO
    assert AUDIO.count("readyPlayers[index].start()") == 1
    assert "if (!readyPlayers.length)" in AUDIO
    assert "if (goatPoolLoading) goatPlayPending = true" in AUDIO
    assert "if (enabled && Date.now() >= crownUntil) playGoat()" in AUDIO
    assert sorted(path.name for path in GOAT_DIR.glob("*.mp3")) == [
        "goat-01.mp3", "goat-02.mp3", "goat-03.mp3", "goat-04.mp3", "goat-05.mp3",
    ]
    assert not list(GOAT_DIR.glob("*.ogg"))
    assert not list(GOAT_DIR.glob("*.wav"))
    assert (GOAT_DIR / "SOURCES.md").is_file()
    assert "http://" not in AUDIO and "https://" not in AUDIO


def test_goat_and_practice_room_are_deliberate_shop_activations_only():
    handler = APP[APP.index("controls.forEach((control)"):APP.index("closeButton.addEventListener", APP.index("controls.forEach((control)"))]
    assert 'control.addEventListener("click"' in handler
    assert 'if (key === "goat") playSound("goatTracker")' in handler
    assert 'if (key === "practice-room") playSound("practiceRoomOpen")' in handler
    assert APP.count('playSound("goatTracker")') == 1
    assert APP.count('playSound("practiceRoomOpen")') == 1
    assert 'data-shop-panel="goat"' in STORE
    assert 'data-shop-panel="practice-room"' in STORE


def test_i_played_it_sound_requires_new_server_confirmation():
    quest = APP[APP.index("function wireQuestForm"):APP.index("const STORE_ITEMS")]
    assert 'fetch("/contests/quest/completions"' not in quest
    assert 'fetch("/contests/bonus-challenge/i-played-it"' in quest
    assert "if (!response.ok)" in quest
    response_at = quest.index("const response = await fetch")
    committed_at = quest.index("payload.created === true")
    sound_at = quest.index('playSound("questCompleted")', committed_at)
    assert response_at < committed_at < sound_at
    assert quest.count('playSound("questCompleted")') == 1


def test_specific_camp_effects_require_created_response_and_hydration_is_silent():
    hours = APP[APP.index("if (hoursCheckbox)"):APP.index("if (careButton)")]
    marching = APP[APP.index("if (marchingButton)"):APP.index("function wirePlungeBurrow")]
    assert "persistedAward.created === true" in hours
    assert 'playSound("bandCampBonus")' in hours
    assert "playCampReward" not in hours
    assert "persistedAward.created === true" in marching
    assert 'playSound("marchingCompleted")' in marching
    assert "playCampReward" not in marching
    assert APP.count('playSound("bandCampBonus")') == 1
    assert APP.count('playSound("marchingCompleted")') == 1
    hydration = APP[APP.index("async function loadPersistedCampAwards"):APP.index("function setButtonComplete")]
    for effect in ("bandCampBonus", "marchingCompleted"):
        assert effect not in hydration


def test_medal_effect_has_explicit_confirmation_hook_but_no_historical_trigger():
    assert 'payload.medal_newly_earned === true' in APP
    assert 'playSound("medalEarned")' in APP
    medal_board = APP[APP.index("function wirePastWinners"):APP.index("function wireHallOfChampions")]
    hall = APP[APP.index("function wireHallOfChampions"):APP.index("function wireShopPolish")]
    assert "medalEarned" not in medal_board
    assert "medalEarned" not in hall


def test_new_effects_share_preferences_master_and_metronome_isolation():
    assert "new Tone.Gain(outputLevel()).toDestination()" in AUDIO
    assert "goatGain = new Tone.Gain(0.35).connect(master)" in AUDIO
    assert "if (!enabled" in AUDIO
    assert "window.localStorage" in AUDIO
    assert "/account/state" not in AUDIO
    metronome = APP[APP.index("function wireMetronome"):APP.index("function wireBandCamp")]
    assert "WoodshedAudio" not in metronome
    assert "Tone" not in metronome
