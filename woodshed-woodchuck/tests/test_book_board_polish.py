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
    verification = group("p-book-verification-option-group")

    for control in (
        "p-book-include-contests",
        "p-book-include-team",
        "p-book-current-team",
    ):
        assert f'id="{control}"' in team
    for control in (
        "p-book-verifier",
        "p-book-verifier-manage",
    ):
        assert f'id="{control}"' in verification

    assert CSS.count(".p-book-option-group {") == 1
    assert "border: 2px solid #527a58" in CSS
    assert ".p-book-option-group .p-book-option-detail" in CSS
    assert "Email your Practice Book" not in BOOK
    assert 'id="p-book-verifier" name="p-book-verifier" aria-label="Verifier"' in BOOK


def test_verification_section_has_compact_copy_and_manager() -> None:
    verification = group("p-book-verification-option-group")
    assert "Verification (optional)" in verification
    assert "Manage Verifiers" in verification
    assert "No verification request" in BOOK
    management = (ROOT / "templates/trusted_verifiers.html").read_text()
    warning = "Please discuss with your Band Director"
    assert warning not in BOOK
    assert warning in management.split('id="band-director-heading"')[1]
    assert 'name="role" value="verifier"' in management
    assert 'name="role" value="band_director"' in management
    assert 'id="trusted-verifier-role"' not in management
    assert 'id="p-book-team-shed-link"' not in BOOK


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
