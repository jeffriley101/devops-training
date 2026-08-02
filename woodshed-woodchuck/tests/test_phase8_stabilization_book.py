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
    assert 'currentEl.textContent = "No team selected"' in script
    assert "No saved recipients yet" in script
    assert "No connected parent or mentor yet." not in script


def test_book_team_section_is_compact_and_shed_owned() -> None:
    template = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "Include this chart in the Team Competition" in template
    assert "Uncheck to prevent being added to this contest." in template
    assert 'id="p-book-current-team"' in template
    assert 'href="/home#shed-team-panel"' in template
    assert "Choose a Team in SHED" in template
    for removed in (
        'id="p-book-team-options"', 'id="p-book-new-team-name"',
        'id="p-book-team-emblem"', 'id="p-book-create-team"',
        "Create and Join Team", "Approved team emblem",
    ):
        assert removed not in template
    assert 'navigate: "/home#shed-team-panel"' in script
    assert "Submit Without Team Competition" in script
    assert "renderTeamEmblem(visual, currentTeam.emblem)" in script


def test_team_boards_render_public_team_only_but_shed_keeps_captain() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    board = script[script.index("function renderTeamBoards"):script.index("function showError", script.index("function renderTeamBoards"))]
    assert "row.team_name" in board and "row.emblem_key" in board
    assert "row.captain_name" not in board
    assert "appendTeamLabel" not in board
    shed = script[script.index("function wireShedTeamBadge"):script.index("async function refreshPracticeStreak")]
    assert "appendTeamLabel(status, current)" in shed
    assert "Team Captain" in script
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
