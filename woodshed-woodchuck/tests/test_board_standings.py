from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "quest.html"


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


def test_board_preserves_placeholders_without_crown_progress() -> None:
    markup = board_template()

    assert "Past Winners" in markup
    assert "Hall of Champions" in markup
    assert "Crown Progress" not in markup


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
