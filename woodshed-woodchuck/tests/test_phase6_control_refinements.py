import base64
from pathlib import Path

import qrcode
from fastapi.testclient import TestClient

from app import main
from app.content import ART_SUBMISSION_EMAIL, SHOP_SHARE_URL


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_secret_period_is_plain_red_with_transparent_hit_target_and_unchanged_flow() -> None:
    css = source("static/css/styles.css")
    javascript = source("static/js/app.js")
    home = source("templates/home.html")
    block = css[css.index(".woodshed-scene > .shed-secret-button {"):css.index(".shed-secret-panel {")]
    assert 'id="shed-secret-button"' in home
    assert '>.</button>' in home
    assert 'type="button"' in home
    assert 'aria-controls="shed-secret-panel"' in home
    assert "color: #d7263d" in block
    assert "background: transparent" in block
    assert "border: 0" in block
    assert "border-radius: 0" in block
    assert "width: 2.25rem" in block and "height: 2.25rem" in block
    assert 'fetch("/account/daily-secret"' in javascript
    assert 'body: JSON.stringify({ passcode: input.value })' in javascript
    assert 'feedback.textContent = payload.redeemed ? "+20 dandelions"' in javascript


def test_secret_period_is_a_scene_anchored_safe_area_bottom_control() -> None:
    css = source("static/css/styles.css")
    home = source("templates/home.html")
    scene = home[
        home.index('<div class="woodshed-scene"'):
        home.index('<div class="woodshed-foreground"')
    ]
    assert 'id="shed-secret-button"' in scene
    assert ".woodshed-scene > .shed-secret-button {" in css
    rule_start = css.index(".woodshed-scene > .shed-secret-button {")
    secret_rule = css[rule_start:css.index("}", rule_start)]
    assert "position: absolute" in secret_rule
    assert "bottom: max(0.45rem, env(safe-area-inset-bottom))" in secret_rule
    assert "left: max(0.45rem, env(safe-area-inset-left))" in secret_rule
    assert "color: #d7263d" in secret_rule
    assert "position: fixed" not in secret_rule



def test_level_control_is_centered_with_full_accessible_dynamic_label() -> None:
    css = source("static/css/styles.css")
    javascript = source("static/js/app.js")
    home = source("templates/home.html")
    block = css[css.index(".shed-readout-level {"):css.index(".shed-secret-button {")]
    assert 'id="level-value"' in home
    assert 'aria-label="Change student level"' in home
    assert 'aria-controls="change-level-panel"' in home
    assert "display: flex" in block and "justify-content: center" in block
    assert "width: 3.5rem" in block and "padding: 0" in block
    assert "`Level: ${profileLevel}. Change level.`" in javascript
    assert 'textContent = profileLevel === "Level not set"' in javascript


def test_shop_dandelion_count_is_visible_unboxed_and_uses_shared_hydration() -> None:
    store = source("templates/store.html")
    css = source("static/css/styles.css")
    javascript = source("static/js/app.js")
    assert '<strong id="credits-value" class="shop-dandelion-count">0</strong>' in store
    assert 'id="dandelion-object"' in store
    block = css[css.index(".shop-dandelion-control"):css.index(".shop-feature-dialog")]
    assert "flex-direction: row" in block
    assert "background" not in block and "border" not in block
    hydrate = javascript[javascript.index("function hydrateHome"):javascript.index("function wireShedSecret")]
    assert 'document.getElementById("credits-value")' in hydrate
    assert "creditsEl.textContent = String(dandelions)" in hydrate
    assert "state.progress.credits ?? 0" in hydrate
    assert javascript.count("hydrateHome(next);") >= 8


def test_trivia_restores_native_checked_and_visual_selected_state() -> None:
    board = source("templates/quest.html")
    javascript = source("static/js/app.js")
    css = source("static/css/styles.css")
    band_camp = javascript[javascript.index("function wireBandCamp"):javascript.index("function wirePlungeBurrow")]
    assert 'id="trivia-selected-answer"' not in board
    assert "Your answer:" not in board + band_camp
    assert "input.checked = choice.id === selectedAnswerId" in band_camp
    assert 'input.type = "radio"' in band_camp
    assert 'input.name = "trivia-answer"' in band_camp
    assert 'label.classList.toggle("is-selected", input.checked)' in band_camp
    assert 'input[name="trivia-answer"]' in band_camp
    assert '"is-confirmed-success",\n          input.checked && daily.triviaAttempted && daily.triviaCorrect' in band_camp
    assert ".trivia-option.is-selected" in css
    assert 'content: "Selected"' in css
    assert "serverConfirmedTriviaAttempt?.selected_answer_id" in band_camp
    assert "+1 Camp Point · +1 dandelion" in band_camp
    assert "Attempt used" in band_camp


def test_bonus_progress_action_is_an_accessible_gold_stereo_dial() -> None:
    board = source("templates/quest.html")
    css = source("static/css/styles.css")
    javascript = source("static/js/app.js")
    assert board.count("quest-stereo-dial") == 1
    for control_id, label in (("complete-quest-btn", "I Played It"),):
        assert f'id="{control_id}"' in board
        assert label in board
        assert f'getElementById("{control_id}")' in javascript
    dial = css[css.index(".quest-stereo-dial {"):css.index(".board-scoreboard {")]
    assert "border-radius: 50%" in dial
    assert "radial-gradient" in dial
    assert "min-height: 7.5rem" in dial
    assert ".quest-stereo-dial:focus-visible" in dial
    assert "outline: 4px solid #fff" in dial


def test_fixed_artist_mailto_has_no_private_fields() -> None:
    assert ART_SUBMISSION_EMAIL == "woodshedwoodchuck@gmail.com"
    assert main.art_submission_mailto() == (
        "mailto:woodshedwoodchuck@gmail.com?subject=Woodshed%20Woodchuck%20Artwork"
    )
    rendered = TestClient(main.app).get("/store").text
    assert main.art_submission_mailto() in rendered
    assert "Artwork email coming soon." not in rendered
    for private_field in ("woodchuck_id", "profile", "account", "session", "bcc="):
        assert private_field not in main.art_submission_mailto().casefold()


def test_fixed_shop_share_url_matches_clipboard_link_and_qr_payload(monkeypatch) -> None:
    assert SHOP_SHARE_URL == "https://woodshed-woodchuck.onrender.com/"
    real_qr_data_uri = main.qr_data_uri
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:9999/private?session=secret")
    captured = []
    monkeypatch.setattr(main, "qr_data_uri", lambda value: captured.append(value) or "data:image/svg+xml;base64,SAFE")
    rendered = TestClient(main.app).get("/store").text
    assert captured == [SHOP_SHARE_URL]
    assert f'data-public-site-url="{SHOP_SHARE_URL}"' in rendered
    assert f'href="{SHOP_SHARE_URL}"' in rendered
    assert "127.0.0.1" not in rendered and "localhost" not in rendered
    assert "Development address" not in rendered

    qr = qrcode.QRCode()
    qr.add_data(SHOP_SHARE_URL)
    qr.make(fit=True)
    decoded = b"".join(segment.data for segment in qr.data_list).decode("utf-8")
    assert decoded == SHOP_SHARE_URL
    uri = real_qr_data_uri(SHOP_SHARE_URL)
    assert b"<svg" in base64.b64decode(uri.split(",", 1)[1])
    assert 'Website address copied.' in source("static/js/app.js")
