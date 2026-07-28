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
