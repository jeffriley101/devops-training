from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
HOME = (ROOT / "templates/home.html").read_text(encoding="utf-8")


def _section(start: str, end: str) -> str:
    return APP_JS[APP_JS.index(start):APP_JS.index(end, APP_JS.index(start))]


def test_water_check_preserves_mum_response_then_launches_digital_rain() -> None:
    mum = _section("function wireMum(state)", "function wireMetronome()")
    assert 'data-mum-choice="water"' in HOME
    assert 'water: "Take a few steady sips.' in mum
    response_position = mum.index("messageEl.textContent = response;")
    launch_position = mum.index("launchDigitalRain(button);")
    assert response_position < launch_position
    assert 'const choice = button.dataset.mumChoice;' in mum
    assert 'if (choice === "water") launchDigitalRain(button);' in mum


def test_digital_rain_is_self_contained_and_stoppable() -> None:
    rain = _section("function launchDigitalRain", "function wireMum(state)")
    assert 'document.createElement("canvas")' in rain
    assert 'document.body.append(overlay)' in rain
    assert "window.requestAnimationFrame(draw)" in rain
    assert 'stopButton.textContent = "STOP"' in rain
    assert 'stopButton.addEventListener("click", stopDigitalRain, { once: true })' in rain
    assert 'event.key === "Escape"' in rain
    assert "window.cancelAnimationFrame(animationFrame)" in rain
    assert "overlay.remove()" in rain
    assert "triggerButton?.focus({ preventScroll: true })" in rain
    assert "Matrix" not in rain


def test_digital_rain_overlay_is_full_screen_and_mobile_tappable() -> None:
    assert ".digital-rain-overlay" in STYLES
    overlay = STYLES[STYLES.index(".digital-rain-overlay"):STYLES.index(".digital-rain-canvas")]
    stop = STYLES[STYLES.index(".digital-rain-stop {"):STYLES.index(".digital-rain-stop:focus-visible")]
    assert "position: fixed" in overlay
    assert "inset: 0" in overlay
    assert "z-index: 10000" in overlay
    assert "min-width: 5.5rem" in stop
    assert "min-height: 3rem" in stop
    assert "env(safe-area-inset-top)" in stop
    assert "touch-action: manipulation" in stop


def test_book_timer_restores_running_state_from_start_timestamp() -> None:
    timer = _section("let practiceTimerStartedAt = null", "function formatEntry(entry)")
    key = 'woodshed:practice-timer-started-at'
    assert f'PRACTICE_TIMER_STORAGE_KEY = "{key}"' in timer
    assert "persistPracticeTimerStart(practiceTimerStartedAt)" in timer
    assert "const restoredStart = readPracticeTimerStart();" in timer
    assert "practiceTimerStartedAt = restoredStart;" in timer
    assert "updatePracticeTimerDisplay();" in timer
    assert "window.setInterval(updatePracticeTimerDisplay, 1000)" in timer
    assert "renderTimerRunning(true)" in timer
    assert 'window.addEventListener("pageshow", restorePracticeTimer)' in timer
    assert 'window.addEventListener("pagehide", stopPracticeTimerInterval)' in timer
    assert "Date.now() - practiceTimerStartedAt" in timer


def test_stopping_or_logging_out_clears_timer_running_state() -> None:
    timer = _section("let practiceTimerStartedAt = null", "function formatEntry(entry)")
    logout = _section("function wireAuthenticatedLogout", "function wireShedDecorations")
    assert timer.count("clearPracticeTimerStart();") == 2
    assert 'window.sessionStorage.removeItem("woodshed:practice-timer-started-at")' in logout
    assert "form.requestSubmit()" not in timer
