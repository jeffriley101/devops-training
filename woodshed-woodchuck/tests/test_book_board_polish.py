from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOK = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
BOARD = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
APP = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
HOME = (ROOT / "templates/home.html").read_text(encoding="utf-8")


def group(name: str) -> str:
    marker = f'<section class="p-book-option-group {name}">'
    start = BOOK.index(marker)
    return BOOK[start:BOOK.index("</section>", start) + len("</section>")]


def test_book_option_groups_enclose_their_existing_controls() -> None:
    team = group("p-book-team-option-group")
    email = group("p-book-email-option-group")
    verification = group("p-book-verification-option-group")

    for control in (
        "p-book-include-contests",
        "p-book-include-team",
        "p-book-current-team",
        "p-book-team-shed-link",
    ):
        assert f'id="{control}"' in team
    for control in (
        "p-book-email-copy",
        "p-book-email-preset",
        "p-book-save-preset",
        "p-book-preset-list",
    ):
        assert f'id="{control}"' in email
    for control in (
        "p-book-request-validation",
        "p-book-verifier",
        "p-book-verifier-manage",
    ):
        assert f'id="{control}"' in verification

    assert CSS.count(".p-book-option-group {") == 1
    assert "border: 2px solid #527a58" in CSS
    assert ".p-book-option-group .p-book-option-detail" in CSS
    assert '<label for="p-book-email-preset">Preset email addresses</label>' not in BOOK
    assert '<label for="p-book-verifier">Connected parent or mentor</label>' not in BOOK
    assert 'id="p-book-email-preset" aria-label="Saved email recipients"' in BOOK
    assert 'id="p-book-verifier" name="p-book-verifier" aria-label="Connected parent or mentor"' in BOOK


def test_preset_email_manager_is_one_column_in_requested_order() -> None:
    email = group("p-book-email-option-group")
    ordered = (
        "Manage preset email address",
        'id="p-book-email-preset"',
        "Do not submit your band director",
        "Recipient Name",
        'id="p-book-preset-name"',
        "Recipient Email",
        'id="p-book-preset-email"',
        'id="p-book-save-preset"',
    )
    positions = [email.index(value) for value in ordered]
    assert positions == sorted(positions)
    assert 'class="mentor-card p-book-email-preset-manager"' in email
    manager_css = CSS[CSS.index(".p-book-email-preset-manager {"):]
    assert "grid-template-columns: minmax(0, 1fr)" in manager_css


def test_closed_board_activity_body_is_removed_from_desktop_layout() -> None:
    closed_rule_start = CSS.index(".board-activity:not([open]) > .board-activity-body {")
    closed_rule = CSS[closed_rule_start:CSS.index("}", closed_rule_start)]
    assert "display: none" in closed_rule

    activity_rule_start = CSS.rindex(".board-activity {")
    activity_rule = CSS[activity_rule_start:CSS.index("}", activity_rule_start)]
    assert "align-self: start" in activity_rule
    assert "min-height: 0" in activity_rule

    mobile_start = CSS.index("@media (max-width: 640px)")
    mobile = CSS[mobile_start:CSS.index("}", CSS.index(".board-activity > summary {", mobile_start)) + 1]
    assert ".board-activity > summary" in mobile
    assert "flex-wrap: wrap" in mobile


def test_bonus_challenge_uses_four_visible_decorative_screws() -> None:
    assert 'class="board-practice-section bonus-challenge-section"' in BOARD
    assert BOARD.count('class="bonus-challenge-screw ') == 4
    assert BOARD.count('aria-hidden="true"') >= 4

    section_start = BOARD.index('class="board-practice-section bonus-challenge-section"')
    section = BOARD[section_start:BOARD.index("</section>", section_start)]
    for position in ("top-left", "top-right", "bottom-left", "bottom-right"):
        assert f"bonus-challenge-screw-{position}" in section

    screw_start = CSS.index(".bonus-challenge-screw {")
    screw_rule = CSS[screw_start:CSS.index("}", screw_start)]
    assert "position: absolute" in screw_rule
    assert "z-index: 3" in screw_rule
    assert "pointer-events: none" in screw_rule
    assert screw_rule.count("linear-gradient") == 2
    assert ".bonus-challenge-section > .bonus-challenge-screw" in CSS
    assert "box-sizing: border-box" in CSS
    for position in ("top-left", "top-right", "bottom-left", "bottom-right"):
        assert f".bonus-challenge-screw-{position}" in CSS


def test_metronome_defaults_and_step_controls_use_four_bpm() -> None:
    metronome = APP[APP.index("function wireMetronome"):APP.index("function wireBandCamp")]
    assert "let bpm = 120;" in metronome
    assert "bpm = 120;" in metronome
    assert 'saved !== null && saved.trim() !== "" && Number.isFinite(Number(saved))' in metronome
    assert "setBpm(bpm - 4);" in metronome
    assert "setBpm(bpm + 4);" in metronome
    assert HOME.count('value="120"') == 2
    assert '<strong id="metronome-bpm-readout">120</strong>' in HOME
    assert "\u22124" in HOME and "+4" in HOME
