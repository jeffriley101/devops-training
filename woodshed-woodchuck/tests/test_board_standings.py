from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


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
    assert "Rank" in markup
    assert "Instrument" in markup
    assert "Practice Minutes" in markup
    assert 'id="contest-open-points"' in markup
    assert 'id="contest-verified-points"' in markup
    assert "Top Five Points Leaders" in markup
    assert "Your Position" in markup


def test_board_preserves_past_winners_and_hall_with_crown_progress() -> None:
    markup = board_template()

    assert "Past Winners" in markup
    assert "Hall of Champions" in markup
    assert "Your Crown Progress" in markup


def test_board_contains_loading_and_empty_states() -> None:
    markup = board_template()

    assert 'id="contest-standings-loading"' in markup
    assert "Loading Band Camp standings" in markup
    assert "No practice has been logged this week yet." in markup
    assert "No verified practice has been approved this week yet." in markup


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
    assert "No finalized Band Camp weeks yet." in markup
    assert "No podium results for this contest and division." in markup
    assert "Weekly Practice Minutes by Instrument" in markup
    assert "Top Five Points Leaders" in markup


def test_past_winners_keeps_live_board_standings_intact() -> None:
    markup = board_template()

    assert markup.count('id="band-camp-standings"') == 1
    assert markup.count('id="contest-open-position"') == 1
    assert markup.count('id="contest-verified-position"') == 1
    assert "Your Position" in markup


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
    assert "Past Winners could not be loaded." in board_template() + javascript


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
    assert 'id="contest-open-position"' in markup
    assert 'id="contest-verified-position"' in markup
    assert 'id="past-winners"' in markup
    assert markup.index('id="hall-of-champions"') > markup.index('id="past-winners"')


def test_shop_and_board_render_accessible_personal_crown_progress() -> None:
    board = board_template()
    shop = STORE_TEMPLATE_PATH.read_text(encoding="utf-8")

    for markup in (board, shop):
        assert 'class="personal-crown-meter"' in markup
        assert 'aria-label="Crown progress: 0 of 10 qualifying wins"' in markup
        assert "0 of 10 wins" in markup
        assert "10 qualifying wins remain." in markup
        assert "Gold wins in Weekly Points Leaders qualify" in markup
        assert "Silver, Bronze, and instrument participation do not count" in markup
    assert 'id="board-crown-title"' in board
    assert 'id="shop-crown-title"' in shop


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
    assert 'id="contest-open-position"' in markup
    assert 'id="contest-verified-position"' in markup
    assert 'id="past-winners"' in markup
    assert markup.index('id="board-crown-title"') < markup.index('id="past-winners"')
