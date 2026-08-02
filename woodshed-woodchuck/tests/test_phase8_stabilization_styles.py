from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
SHED = (ROOT / "templates/home.html").read_text(encoding="utf-8")
BOARD = (ROOT / "templates/quest.html").read_text(encoding="utf-8")


def test_authoritative_emblem_renderer_handles_shield_letter_and_emoji() -> None:
    renderer = APP[APP.index("function renderTeamEmblem"):APP.index("function appendTeamLabel")]
    assert "team-emblem-shield" in renderer
    assert "team-emblem-letter" in renderer
    assert "team-emblem-emoji" in renderer
    assert "visual.textContent = normalized.value" in renderer
    assert "normalized.value.toLowerCase()" in renderer
    assert ".team-emblem-shield-gold" in CSS
    assert "linear-gradient" in CSS[CSS.index(".team-emblem-shield-gold"):]
    shed_loader = APP[APP.index("function wireShedTeamBadge"):APP.index("async function refreshPracticeStreak")]
    assert "renderTeamEmblem(emblem, current?.emblem" in shed_loader
    assert "emblem.textContent = current?.emblem?.value" not in shed_loader


def test_captain_star_and_accessible_label_are_shared() -> None:
    label = APP[APP.index("function appendTeamLabel"):APP.index("const questPool")]
    assert 'aria-hidden="true">⭐' in label
    assert 'accessible.textContent = " Team Captain"' in label
    assert "function createShedTeamCard" in label
    assert 'star.textContent = "⭐ "' in label


def test_member_since_is_only_on_board_beneath_name() -> None:
    assert "Member Since" not in SHED
    name_at = BOARD.index('id="board-player-name"')
    member_at = BOARD.index("Member Since")
    weekly_at = BOARD.index("This Week’s Camp Points")
    assert name_at < member_at < weekly_at
    assert "Member’s Since" not in BOARD
    assert 'datetime="{{ member_since.timestamp }}"' in BOARD


def test_board_heading_cherry_description_and_team_contrast_hooks() -> None:
    assert BOARD.count("PRACTICE MINUTES LEADERBOARD") >= 3
    assert 'contest-description-plain">All submitted P-Charts' in BOARD
    assert "contest-description-cherry" not in BOARD
    plain_at = CSS.index(".contest-description-plain")
    plain = CSS[plain_at:plain_at + 150]
    assert "background: transparent" in plain
    team = CSS[CSS.index(".team-leaderboard-card {"):CSS.index(".medal-board,")]
    assert "color: #38260f" in team
    assert ".contest-empty-state" in team


def test_book_fields_share_one_warm_focusable_field_family() -> None:
    section = CSS[CSS.index('#p-book-form input:not([type="checkbox"]'):
                  CSS.index(".team-tile-grid")]
    assert "#p-book-form select" in section
    assert "#p-book-form textarea" in section
    assert "background: #fff1b8" in section
    assert "border-radius: 10px" in section
    assert ":focus-visible" in section
    assert ".p-book-option-card + .p-book-option-detail" in CSS
