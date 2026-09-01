from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "templates/home.html").read_text(encoding="utf-8")
BOOK = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
BOARD = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
SHOP = (ROOT / "templates/store.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")


def test_mum_artwork_is_used_only_inside_the_existing_mum_experience() -> None:
    panel = HOME[HOME.index('id="mum-panel"'):]
    assert '/static/img/characters/mum.png' in panel
    assert '/static/img/characters/mum-corner.png' in panel
    assert panel.index("mum-presence-art") < panel.index("mum-corner-art")
    assert 'id="mum-message"' in panel
    assert 'id="mum-snack-chooser"' in panel
    assert 'id="mum-ready-button"' in panel


def test_whistler_and_john_are_decorative_at_their_existing_product_surfaces() -> None:
    timer = BOOK[BOOK.index('class="mentor-card practice-timer-metal"'):BOOK.index('</article>')]
    assert '/static/img/characters/whistler.png' in timer
    assert 'class="p-book-whistler-art"' in timer
    assert 'alt=""' in timer and 'aria-hidden="true"' in timer

    bonus = BOARD[BOARD.index('class="board-practice-section bonus-challenge-section"'):]
    assert '/static/img/characters/john.png' in bonus
    assert 'class="bonus-challenge-john-art"' in bonus
    assert bonus.index('id="complete-quest-btn"') < bonus.index("bonus-challenge-john-art")
    assert 'alt=""' in bonus and 'aria-hidden="true"' in bonus


def test_character_art_is_responsive_and_cannot_intercept_controls() -> None:
    for asset in ("mum.png", "mum-corner.png", "whistler.png", "john.png"):
        path = ROOT / "static" / "img" / "characters" / asset
        assert path.is_file() and path.stat().st_size > 0
    assert ".mum-artwork img" in CSS
    assert ".p-book-whistler-art" in CSS
    assert ".bonus-challenge-john-art" in CSS
    assert CSS.count("pointer-events: none;") >= 3
    narrow = CSS[CSS.index("@media (max-width: 430px)"):]
    assert ".mum-artwork" in narrow
    assert ".p-book-whistler-art" in narrow
    assert ".bonus-challenge-john-art" in narrow


def test_shop_artwork_remains_approved_and_unrelated_viking_is_not_referenced() -> None:
    combined = "\n".join((HOME, BOOK, BOARD, SHOP, CSS))
    assert '/static/img/shop3.png' in SHOP
    assert "Viking at the Whimsical Valley Fair.png" not in combined
