from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_removed_preset_elements_cannot_abort_book_initialization() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "teacherEmailOptionsEl" not in script
    assert "parentEmailOptionsEl" not in script
    assert "renderEmailOptions(state)" not in script
    assert "const initializeFeature" in script
    for initializer in (
        "wirePracticeTimer", "loadVerifierOptions", "loadTeams",
        "loadEmailPresets", "loadPersistentPracticeCharts", "loadPracticeTotals",
    ):
        assert f"initializeFeature({initializer}" in script


def test_book_wiring_is_idempotent_and_timer_is_independent() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert 'form.dataset.woodshedPBookWired === "true"' in script
    assert 'form.dataset.woodshedPBookWired = "true"' in script
    timer = script[script.index("function wirePracticeTimer"):script.index("function formatEntry")]
    assert "stopPracticeTimerInterval();" in timer
    assert "window.setInterval(updatePracticeTimerDisplay, 1000)" in timer
    assert 'window.addEventListener("pagehide", stopPracticeTimerInterval' in timer
    assert "metronome" not in timer.casefold()
    assert "fetch(" not in timer


def test_book_loaders_resolve_empty_and_failure_states() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "No team selected yet." in script
    assert "No saved recipients yet" in script
    assert "No connected parent or mentor yet." in script
    assert "Saved recipients unavailable" in script
    assert "Teams could not be loaded." in script
    assert "Trusted verifiers unavailable" in script


def test_submission_keeps_warnings_confirmation_and_single_clipboard_attempt() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    for label in (
        "Submit Without Team Competition", "Submit Without Emailing",
        "Submit Without Validation Request", "Submit this P-Chart",
    ):
        assert label in script or label in (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    assert "if (submissionInFlight) return" in script
    assert "if (createdPayload.created === true)" in script
    assert script.count("await navigator.clipboard.writeText(exportText)") == 1
    assert "No duplicate actions were performed." in script
