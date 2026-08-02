from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.instruments import INSTRUMENT_DEFINITIONS


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "quest.html"
STORE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "store.html"


def board_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_board_contains_live_standings_markup() -> None:
    markup = board_template()

    assert 'id="band-camp-standings"' in markup
    assert 'id="contest-division-tabs"' in markup
    assert 'id="contest-open-tab"' in markup
    assert 'id="contest-verified-tab"' in markup
    assert 'aria-selected="true"' in markup
    assert 'role="tabpanel"' in markup
    assert 'class="contest-ranked-list"' in markup
    assert 'role="list"' in markup
    assert 'id="contest-open-points"' in markup
    assert 'id="contest-verified-points"' in markup
    assert "PRACTICE MINUTES LEADERBOARD" in markup
    assert 'aria-label="Open minutes leaders"' in markup
    assert 'aria-label="Verified minutes leaders"' in markup
    assert 'aria-label="Open points leaders"' not in markup
    assert 'aria-label="Verified points leaders"' not in markup
    assert "WEEKLY PRACTICE BY INSTRUMENT" in markup
    assert "WEEKLY BAND CAMP POINTS" in markup
    assert 'id="contest-open-camp-points"' in markup
    assert 'id="contest-open-camp-position"' not in markup
    assert 'id="contest-verified-camp-points"' not in markup
    assert "Your Position" not in markup


def test_board_preserves_past_winners_and_hall_without_personal_crown() -> None:
    markup = board_template()

    assert "Past Winners" in markup
    assert "Hall of Champions" in markup
    assert "Your Crown Progress" not in markup
    assert "personal-crown-progress" not in markup


def test_board_contains_loading_and_empty_states() -> None:
    markup = board_template()

    assert 'id="contest-standings-loading"' in markup
    assert "Loading Band Camp standings" in markup
    assert "No P-Charts have been submitted this week yet." in markup
    assert "No verified P-Charts have been approved this week yet." in markup
    assert 'id="contest-standings-error"' in markup
    assert 'id="contest-standings-retry"' in markup


def test_board_authentication_behavior_is_unchanged() -> None:
    response = TestClient(app).get("/quest")

    assert response.status_code == 200
    assert "Band Camp Standings" in response.text


def test_board_template_contains_no_private_account_fields() -> None:
    markup = board_template().casefold()

    for private_field in (
        "woodchuck_id",
        "pin_hash",
        "legal_name",
        "email_address",
        "verifier_id",
        "verifier_name",
        "verifier_email",
    ):
        assert private_field not in markup


def test_fake_local_point_leader_is_not_presented() -> None:
    markup = board_template()

    assert "Current Point Leader" not in markup
    assert 'id="board-leader-name"' not in markup
    assert 'id="board-leader-points"' not in markup


def test_board_contains_past_winners_medal_board_states_and_navigation() -> None:
    markup = TestClient(app).get("/quest").text

    for element_id in (
        "past-winners", "past-winners-loading", "past-winners-auth",
        "past-winners-empty", "past-winners-error", "past-winners-retry",
        "past-winners-week", "past-winners-open-tab",
        "past-winners-verified-tab", "past-winners-open-panel",
        "past-winners-verified-panel",
    ):
        assert f'id="{element_id}"' in markup
    assert "Medals will appear after a Band Camp week is finalized." in markup
    assert "No podium results for this contest and division." in markup
    assert "WEEKLY PRACTICE BY INSTRUMENT" in markup
    assert "PRACTICE MINUTES LEADERBOARD" in markup


def test_past_winners_keeps_live_board_standings_intact() -> None:
    markup = board_template()

    assert markup.count('id="band-camp-standings"') == 1
    assert 'id="contest-open-position"' not in markup
    assert 'id="contest-verified-position"' not in markup
    assert "Your Position" not in markup


def test_past_winners_javascript_handles_medals_ties_and_failures() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'fetch("/contests/weeks/finalized"' in javascript
    assert "encodeURIComponent(weekStart)" in javascript
    assert '1: { key: "gold", emoji: "🥇"' in javascript
    assert '2: { key: "silver", emoji: "🥈"' in javascript
    assert '3: { key: "bronze", emoji: "🥉"' in javascript
    assert "results.filter" in javascript
    assert "rows.forEach" in javascript
    assert "result.rank" in javascript
    assert "Medal Board of Past Winners could not be loaded." in board_template() + javascript


def test_board_contains_hall_panels_filters_states_and_show_all() -> None:
    markup = board_template()

    for element_id in (
        "hall-of-champions", "champions-loading", "champions-auth",
        "champions-empty", "champions-error", "champions-retry",
        "champions-division-filters", "student-champions-list",
        "instrument-champions-list", "student-champions-show-all",
        "instrument-champions-show-all",
    ):
        assert f'id="{element_id}"' in markup
    for division in ("all", "open", "verified"):
        assert f'data-champions-division="{division}"' in markup
    assert "Student Champions" in markup
    assert "Instrument Champions" in markup
    assert "No finalized champions yet." in markup


def test_hall_javascript_uses_top_ten_filters_medals_crown_and_retry() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'fetch("/contests/hall-of-champions"' in javascript
    assert "ordered.slice(0, 10)" in javascript
    assert 'division === "all"' in javascript
    assert 'medalStat("🥇", "Gold"' in javascript
    assert 'medalStat("🥈", "Silver"' in javascript
    assert 'medalStat("🥉", "Bronze"' in javascript
    assert "Permanent crown earned" in javascript
    assert "champions-show-all" in javascript
    assert 'retryButton.addEventListener("click", loadChampions)' in javascript


def test_hall_preserves_existing_board_sections() -> None:
    markup = board_template()

    assert 'id="band-camp-standings"' in markup
    assert 'id="contest-open-position"' not in markup
    assert 'id="contest-verified-position"' not in markup
    assert 'id="past-winners"' in markup
    assert markup.index('id="hall-of-champions"') > markup.index('id="past-winners"')


def test_shop_renders_accessible_personal_crown_progress() -> None:
    board = board_template()
    shop = STORE_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'class="personal-crown-meter"' in shop
    assert 'aria-label="Crown progress: 0 of 10 qualifying wins"' in shop
    assert "0 of 10 wins" in shop
    assert "10 qualifying wins remain." in shop
    assert "Gold wins in Top Five Minutes Leaders qualify" in shop
    assert "Silver, Bronze, and instrument participation do not count" in shop
    assert 'id="board-crown-title"' not in board
    assert 'data-shop-panel="crown"' in shop
    assert 'data-shop-panel-content="crown"' in shop


def test_crown_javascript_handles_earned_unearned_date_and_above_ten() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'fetch("/contests/crown-progress"' in javascript
    assert "Math.min(wins, target)" in javascript
    assert "Permanent crown earned" in javascript
    assert "progress never resets" in javascript
    assert "qualifying ${winsRemaining === 1 ? \"win\" : \"wins\"} remain" in javascript
    assert "Earned ${formattedDate}" in javascript


def test_crown_display_preserves_hall_live_standings_and_past_winners() -> None:
    markup = board_template()

    assert 'id="hall-of-champions"' in markup
    assert 'id="band-camp-standings"' in markup
    assert 'id="contest-open-position"' not in markup
    assert 'id="contest-verified-position"' not in markup
    assert 'id="past-winners"' in markup
    assert 'id="board-crown-title"' not in markup


def test_rendered_pages_and_application_sources_have_no_conflict_markers() -> None:
    markers = ("<" * 7, "=" * 7, ">" * 7)
    client = TestClient(app)
    for path in ("/", "/login", "/setup", "/home", "/p-book", "/quest", "/store"):
        response = client.get(path)
        assert response.status_code == 200
        assert not any(marker in response.text for marker in markers)

    root = Path(__file__).resolve().parents[1]
    for directory in ("app", "templates", "static"):
        for source in (root / directory).rglob("*"):
            if source.suffix not in {".py", ".html", ".css", ".js"}:
                continue
            text = source.read_text(encoding="utf-8")
            assert not any(marker in text for marker in markers), source


def test_live_scoreboard_javascript_uses_actual_ranks_and_preserves_ties() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert "rank.textContent = String(row.rank)" in javascript
    assert "`Rank ${row.rank}, ${publicName}" in javascript
    assert "`Rank ${row.rank}, ${teamName || row.instrument}" in javascript
    assert "Math.min(row.rank, 4)" in javascript
    assert "position.tied === true" in javascript
    assert "position.in_top_five === false" in javascript
    assert ': `${scoreValue} min`' in javascript
    assert ': `${behind} min behind leader`' in javascript


def test_instrument_standings_use_collective_team_labels() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")
    instruments = (
        Path(__file__).resolve().parents[1] / "app" / "instruments.py"
    ).read_text(encoding="utf-8")

    assert "WWInstruments.teamLabel(row.instrument)" in javascript
    assert "WWInstruments.teamLabel(subject)" in javascript
    assert "WWInstruments.teamLabel(champion.instrument_label)" in javascript
    for team_label in (
        "The Clarinets", "The Tubas", "The Percussion",
        "The Drum Majors", "The Color Guard",
    ):
        assert f'team_label="{team_label}"' in instruments
    assert all(
        isinstance(item["team_label"], str) and item["team_label"].startswith("The ")
        for item in INSTRUMENT_DEFINITIONS
    )


def test_live_scoreboard_switching_refresh_and_retry_are_wired() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'event.key === "ArrowRight"' in javascript
    assert 'event.key === "ArrowLeft"' in javascript
    assert "selectDivision(selectedDivision, false)" in javascript
    assert "if (requestInFlight) {" in javascript
    assert "refreshQueued = true" in javascript
    assert 'retryButton.addEventListener("click", loadStandings)' in javascript
    assert 'window.addEventListener("ww:p-chart-saved", loadStandings)' in javascript
    assert 'window.dispatchEvent(new CustomEvent("ww:p-chart-saved"))' in javascript
    assert 'window.addEventListener("ww:camp-points-saved", loadStandings)' in javascript


def test_camp_point_actions_persist_and_guard_duplicate_clicks() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'fetch("/contests/camp-points/awards"' in javascript
    assert "campAwardsInFlight.has(activityType)" in javascript
    assert 'await persistCampPoint("hours")' in javascript
    assert 'await persistCampPoint("care")' in javascript
    assert 'fetch("/contests/trivia/answer"' in javascript
    assert 'serverConfirmedAwards.add("trivia")' in javascript
    assert 'await persistCampPoint("marching")' in javascript
    assert 'activity_date: today' in javascript
    assert 'new CustomEvent("ww:camp-points-saved")' in javascript
    assert 'standings["weekly-camp-points"]' in javascript
    assert '"Camp points"' in javascript


def test_completed_band_camp_activities_use_server_backed_disclosures() -> None:
    root = Path(__file__).resolve().parents[1]
    markup = (root / "templates" / "quest.html").read_text(encoding="utf-8")
    javascript = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
    css = (root / "static" / "css" / "styles.css").read_text(encoding="utf-8")

    assert markup.count('<details id="') >= 4
    assert markup.count("<summary>") >= 4
    for activity in ("camp-hours", "instrument-care", "trivia", "marching"):
        assert f'id="{activity}-activity"' in markup
    assert '`/contests/camp-points/awards/${encodeURIComponent(today)}`' in javascript
    assert "serverConfirmedAwards.has(activityType)" in javascript
    assert "details.open = false" in javascript
    assert "Leave activities open when server completion cannot be confirmed" in javascript
    assert 'content: "▶"' in css


def test_board_weekly_points_and_hours_checkbox_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    markup = (root / "templates" / "quest.html").read_text(encoding="utf-8")
    javascript = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
    css = (root / "static" / "css" / "styles.css").read_text(encoding="utf-8")
    weekly = markup.index("This Week’s Camp Points:")
    career = markup.index("Career Band Camp Points:")
    hours_panel = markup[
        markup.index('id="camp-hours-activity"'):
        markup.index('id="instrument-care-activity"')
    ]

    assert weekly < career
    assert 'id="board-player-weekly-points">0</strong>' in markup
    assert "Were you at band camp or mini-camp today?" in hours_panel
    assert 'id="camp-hours-checkbox" type="checkbox"' in hours_panel
    assert 'for="camp-hours-checkbox"' in hours_panel
    assert 'type="number"' not in hours_panel
    assert "Added to Board" not in hours_panel + javascript
    assert '.camp-hours-checkbox-label input[type="checkbox"]' in css
    assert "width: 2rem" in css and "height: 2rem" in css
    assert 'const persistedAward = await persistCampPoint("hours")' in javascript
    assert "persistedAward.created === true" in javascript
    assert "hoursCheckbox.checked = false" in javascript
    assert "hoursActivity.open = true" in javascript
    assert 'serverConfirmedAwards.has("hours")' in javascript


def test_past_winners_renders_weekly_camp_points() -> None:
    markup = TestClient(app).get("/quest").text
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'id="past-winners-open-camp-points"' in markup
    assert 'id="past-winners-verified-camp-points"' in markup
    assert 'renderContest(division, "weekly-camp-points", results)' in javascript


def test_live_scoreboard_week_header_icons_and_status_are_present() -> None:
    markup = board_template()
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'id="contest-week-range"' in markup
    assert 'id="contest-week-context"' in markup
    assert 'id="contest-week-status"' in markup
    assert "endDate.setDate(endDate.getDate() - 1)" in javascript
    assert "window.WWInstruments.getDefinition(row.instrument)" in javascript
    assert "definition.fallback_symbol" in javascript
    assert 'weekStatusEl.classList.add("hidden")' in javascript


def test_completed_p_chart_posts_once_and_refreshes_history_and_standings() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'method: "POST"' in javascript
    assert 'submission_key: submissionKey' in javascript
    assert "verifierId: verifierId || null" in javascript
    assert "if (submissionInFlight) return" in javascript
    assert "pendingSubmissionKey = pendingSubmissionKey ||" in javascript
    assert "await loadPersistentPracticeCharts()" in javascript
    assert 'new CustomEvent("ww:p-chart-saved")' in javascript


def test_draft_account_state_saving_does_not_call_chart_creation() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    submit_handler = javascript[javascript.index('form.addEventListener("submit"') :]
    assert 'createPersistentPracticeChart({' in submit_handler
    assert javascript.count('await createPersistentPracticeChart({') == 1
