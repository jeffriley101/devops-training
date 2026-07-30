from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]


def test_level_data_migration_and_downgrade_preserve_meaning(tmp_path, monkeypatch) -> None:
    database = tmp_path / "levels.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "6f4d8b0c2a11")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO woodchuck_profiles
              (id, woodchuck_id, display_name, pin_hash, instrument, level, goal,
               created_at, updated_at, display_name_changed_at, level_changed_at)
            VALUES
              (1, 'WC-COLLEGE', 'College Student', 'private', 'Flute', 'College', 'Practice',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, '2026-07-01 12:00:00'),
              (2, 'WC-CONSERV', 'Conservatory Student', 'private', 'Tuba', 'Conservatory', 'Practice',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, '2026-07-02 12:00:00'),
              (3, 'WC-OTHER', 'Other Student', 'private', 'Clarinet', 'Advanced', 'Practice',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, '2026-07-03 12:00:00')
        """))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT woodchuck_id, level, level_changed_at, instrument FROM woodchuck_profiles ORDER BY id"
        )).all()
    assert rows == [
        ("WC-COLLEGE", "Honors", "2026-07-01 12:00:00", "Flute"),
        ("WC-CONSERV", "College", "2026-07-02 12:00:00", "Tuba"),
        ("WC-OTHER", "Advanced", "2026-07-03 12:00:00", "Clarinet"),
    ]
    command.downgrade(config, "6f4d8b0c2a11")
    with engine.connect() as connection:
        levels = connection.execute(text(
            "SELECT level FROM woodchuck_profiles ORDER BY id"
        )).scalars().all()
    assert levels == ["College", "Conservatory", "Advanced"]


def test_book_definition_position_magenta_button_and_timer_cleanup() -> None:
    book = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    definition = (
        "Practice counts whenever you give your instrument real attention—playing it, cleaning it, fingering through music, "
        "or thinking about what you want to improve. Thinking-only time counts as half-minutes. We trust you to keep it honest!"
    )
    content = (ROOT / "app/content.py").read_text(encoding="utf-8")
    assert definition not in book
    assert definition in content
    assert "practice-minutes-definition" not in book
    assert "p-book-verifier-manage" in book
    assert "#c72c83" in css and "#83124f" in css
    assert ".p-book-action-column .btn,\n.p-book-verifier-manage" in css
    assert "font-size: 1rem" in css
    assert "Stop the timer to fill in your minutes" not in book
    assert 'id="practice-timer-toggle-btn"' in book
    assert 'id="p-book-minutes"' in book
    assert 'id="practice-timer-feedback"' in book


def test_bonus_challenge_uses_metallic_gold_interior() -> None:
    board = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert "🏆" in board and "Bonus Challenge" in board
    assert "metallic-gold interior" in css
    assert ".board-practice-section.bonus-challenge-section > .section-head" in css
    for color in ("#8f5b09", "#e2ae32", "#fff0a0", "#bd7c0c", "#f1c64d"):
        assert color in css
