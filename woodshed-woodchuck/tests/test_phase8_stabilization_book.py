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
    assert "Saved recipients unavailable" in script
    assert "Teams could not be loaded." in script
    assert "Trusted verifiers unavailable" in script


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
    assert "active_member_count" not in board
    shed = script[script.index("function wireShedTeamBadge"):script.index("async function refreshPracticeStreak")]
    assert "createShedTeamCard(current, {current: true})" in shed
    assert "Team Captain" in script


def test_practice_book_title_and_work_disclosure_copy() -> None:
    template = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert '<h2 class="p-book-title">Practice Book</h2>' in template
    assert "Captain's Practice Log" not in template
    assert "Captain’s Practice Log" not in template + css
    label_at = template.index('class="p-book-work-label">What did you work on?</label>')
    details_at = template.index('class="mentor-card p-book-work-field"')
    summary_at = template.index("<summary>Check anything that fits this practice chart.</summary>")
    assert label_at < details_at < summary_at
    assert template.count("What did you work on?") == 1
    assert template.count('name="practice-detail"') >= 20
    assert "practiceDetailEls" in (ROOT / "static/js/app.js").read_text(encoding="utf-8")


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


def test_book_uses_responsive_metallic_spiral_layout_and_scoped_controls() -> None:
    template = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert 'class="card p-book-page"' in template
    assert 'class="p-book-notebook"' in template
    assert 'class="p-book-spiral" aria-hidden="true"' in template
    assert "grid-template-columns: minmax(0, 1fr) 46px minmax(0, 1fr)" in css
    assert "@media (max-width: 760px)" in css
    assert ".p-book-spiral { min-height: 42px" in css
    assert ".p-book-page .practice-stat-stone" in css
    assert ".p-book-page .pirate-logbook" in css
    assert ".p-book-page .btn" in css
    assert ".p-book-page #p-book-team-shed-link" in css
    assert ".p-book-page .p-book-verifier-manage" in css
    assert ".p-book-page #submit-p-chart-btn" in css
    assert "border-radius: 50%" in css
    assert ".p-book-page #submit-p-chart-btn.p-book-submit-gold" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "body .btn" not in css


def test_book_history_uses_accessible_verified_and_pristine_badges() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    formatter = script[script.index("function formatEntry"):script.index("function renderEntries")]
    assert 'class="p-book-entry-badge p-book-verified-badge"' in formatter
    assert 'aria-label="Verified" title="Verified">V</span>' in formatter
    assert 'class="p-book-entry-badge p-book-pristine-badge"' in formatter
    assert 'aria-label="Pristine P-Chart" title="Pristine P-Chart">🥇</span>' in formatter
    assert "`${verificationText}${pristineText}${verifierNoteText}`" in formatter


def test_book_asset_versions_are_advanced() -> None:
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    assert "/static/css/styles.css?v=68" in base
    assert "/static/js/app.js?v=33" in base
    assert "styles.css?v=67" not in base
    assert "app.js?v=32" not in base


def test_timer_status_and_history_have_independent_full_width_rows() -> None:
    template = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    timer_row = template.index('class="practice-timer-row"')
    status_row = template.index('class="body-copy practice-timer-status"')
    assert timer_row < status_row
    assert ".p-book-page .practice-timer-status" in css
    timer_css = css[css.index(".p-book-page .practice-timer-status"):]
    assert "position: static" in timer_css[:300]
    assert "grid-row: 2" in timer_css[:300]
    heading = template.index('<h2 class="p-book-title">Practice Book</h2>')
    entries = template.index('id="p-book-entries" class="p-book-entry-list"')
    assert heading < entries
    assert ".p-book-page .pirate-logbook {\n  display: block;" in css
    assert ".p-book-page .p-book-entry-list" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css[css.index(".p-book-page .p-book-entry-list"):][:220]


def test_shed_team_selector_has_one_current_card_and_polished_emblems() -> None:
    template = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert template.count('id="shed-team-current"') == 1
    assert "Join Another Team" in template
    assert 'id="shed-team-other-section"' in template
    assert "New team name" in template
    assert "Choose an emblem" in template
    assert 'id="shed-team-emblem-preview"' in template
    assert "otherTeams = (payload.teams || []).filter((team) => team.id !== current?.id)" in script
    assert "otherSection.hidden = otherTeams.length === 0" in script
    assert 'document.createTextNode("Captain: ")' in script
    assert 'star.textContent = "⭐ "' in script
    assert 'accessible.textContent = " Team Captain"' in script
    assert "Letter ${normalized.value}" in script
    assert "Shield`" in script
    assert "${item.value} ${item.key}" not in script
    assert "emblemChoice.append(new Option(emblemDisplayName(item), item.key))" in script
    assert "if (emblem && emblem.key)" in script
    assert 'cat: "Cat"' in script and 'dog: "Dog"' in script
    assert ".team-radio-native" in css and "clip-path: inset(50%)" in css
    assert ".shed-team-choice-card.is-selected" in css


def test_work_options_use_bounded_grid_without_compression() -> None:
    template = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert template.count('class="practice-work-option"') == template.count('name="practice-detail"')
    assert template.count('name="practice-detail"') >= 20
    assert '<summary>Check anything that fits this practice chart.</summary>' in template
    final_rules = css[css.index("/* Clean, left-aligned practice checkbox list */"):]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in final_rules
    assert "@container (max-width: 30rem)" in final_rules
    assert ".p-book-page .practice-detail-grid { grid-template-columns: minmax(0, 1fr); }" in final_rules
    assert "column-count" not in final_rules
    assert ".practice-work-option > span" in final_rules


def test_bonus_loading_state_always_resolves_to_action_or_error() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    quest = script[script.index("function wireQuestForm"):script.index("const STORE_ITEMS")]
    assert 'completeBtn.textContent = "Loading Challenge…"' in quest
    assert 'completeBtn.textContent = "I Played It"' in quest
    assert 'completeBtn.textContent = "Unavailable"' in quest
    assert "const controller = new AbortController()" in quest
    assert "window.setTimeout(() => controller.abort(), 10000)" in quest
    assert "window.clearTimeout(timeoutId)" in quest
    assert quest.count('form.addEventListener("submit"') == 1
