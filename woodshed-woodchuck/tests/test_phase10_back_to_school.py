from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.board_seasons import BOARD_SEASONS, board_season_for_date
from app.contest_seasons import SEASON_KEY_PATTERN
from app.contests import (
    EXPANDED_TRIVIA_START,
    LEGACY_TRIVIA_QUESTION_COUNT,
    TRIVIA_QUESTIONS,
    public_trivia_question,
    trivia_question_for,
)
from app.main import app


ROOT = Path(__file__).resolve().parents[1]
BOARD = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
APP = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")


def test_back_to_school_is_season_two_board_content() -> None:
    assert [season.key for season in BOARD_SEASONS[:2]] == [
        "band-camp", "back-to-school",
    ]
    assert board_season_for_date(date(2026, 8, 2)).title == "Band Camp"
    assert board_season_for_date(date(2026, 8, 3)).title == "Back to School"
    assert SEASON_KEY_PATTERN.fullmatch("back-to-school-2026")
    assert TestClient(app).get("/quest").text.count('aria-label="Back to School"') == 1


def test_four_normal_activities_are_collapsed_mobile_safe_lockers() -> None:
    openings = re.findall(
        r'<details id="(?:camp-hours|instrument-care|trivia|marching)-activity"[^>]*>',
        BOARD,
    )
    assert len(openings) == 4
    assert all("board-locker" in opening and " open" not in opening for opening in openings)
    assert ".board-locker > summary" in CSS
    assert "min-height: 7.25rem" in CSS
    assert "@media (max-width: 640px)" in CSS
    assert "else if (!complete)" not in APP
    assert 'details.open = false' in APP


def test_back_to_school_activity_copy_and_rotation_preserve_reward_keys() -> None:
    assert "Rehearsal / Lesson" in BOARD
    assert "Did you have rehearsal or a lesson outside of school hours today?" in BOARD
    assert "Instrument Care" in BOARD
    assert "Daily Trivia" in BOARD
    assert "Readiness Challenge" in BOARD
    readiness = APP[
        APP.index("const BACK_TO_SCHOOL_READINESS_CHALLENGES"):
        APP.index("function wireBandCamp")
    ]
    assert readiness.count('    "') == 3
    for concept in ("reeds", "music, a pencil", "remove the reed"):
        assert concept in readiness
    assert "dayIndex % BACK_TO_SCHOOL_READINESS_CHALLENGES.length" in APP
    for activity in ('"hours"', '"care"', '"trivia"', '"marching"'):
        assert activity in APP


def test_trivia_pool_is_expanded_and_keeps_public_answer_contract() -> None:
    assert len(TRIVIA_QUESTIONS) >= 20
    assert len({question["id"] for question in TRIVIA_QUESTIONS}) == len(TRIVIA_QUESTIONS)
    public = public_trivia_question(date(2026, 8, 18))
    assert set(public) == {"id", "question", "choices"}
    assert "correct_answer_id" not in repr(public)
    assert all(set(choice) == {"id", "text"} for choice in public["choices"])
    historical_date = date(2026, 8, 22)
    assert trivia_question_for(historical_date) == TRIVIA_QUESTIONS[
        historical_date.timetuple().tm_yday % LEGACY_TRIVIA_QUESTION_COUNT
    ]
    assert trivia_question_for(EXPANDED_TRIVIA_START) == TRIVIA_QUESTIONS[
        LEGACY_TRIVIA_QUESTION_COUNT
    ]


def test_streamers_burrow_launcher_and_bonus_challenge_contract() -> None:
    assert 'class="back-to-school-streamers" aria-hidden="true"' in BOARD
    assert ".back-to-school-streamers span" in CSS
    assert 'id="plunge-burrow-button"' in BOARD
    assert "Highest Burrow Score to Date" not in BOARD
    assert 'id="board-player-burrow-best"' not in BOARD
    assert 'id="board-burrow-leaderboard"' not in BOARD

    bonus = BOARD[BOARD.index('class="board-practice-section bonus-challenge-section"'):]
    assert "🏆</span> Bonus Challenge" in bonus
    assert 'id="quest-text"' in bonus
    assert 'id="complete-quest-btn"' in bonus
    assert "I Played It" in bonus
    assert "board-locker" not in bonus


def test_hall_ui_separates_weekly_history_from_lifetime_medal_leaders() -> None:
    assert "Historical winners" not in BOARD
    assert '<summary id="past-winners-title">Medal Board of Past Winners</summary>' in BOARD
    assert '<summary id="hall-of-champions-title">Hall of Champions</summary>' in BOARD
    assert BOARD.count("history-disclosure") >= 2
    assert "Seasonal history" not in BOARD
    assert 'id="lifetime-champions-list"' in BOARD
    assert "All-Time Medal Leaders" in BOARD
    hall = APP[APP.index("function wireHallOfChampions"):APP.index("function wirePersonalCrownProgress")]
    assert 'function renderLifetimeHall()' in hall
    assert 'renderLifetimeLeaders("Woodchucks", "students")' in hall
    assert 'renderLifetimeLeaders("Teams", "teams")' in hall
    assert 'renderSpecialChampionships()' in hall


def test_live_contests_are_collapsible_categories_with_future_divisions() -> None:
    rendered = TestClient(app).get("/quest").text
    assert rendered.count('class="contest-category-card"') == 8
    assert rendered.count('data-division="open"') == 4
    assert rendered.count('data-division="verified"') == 4
    assert rendered.count('data-division="pristine"') == 4
    assert 'class="contest-division-card contest-division-coming-soon"' not in rendered
    assert "Open · Verified · Pristine 🚧 · MVP 🚧" not in rendered
    for category in (
        "weekly-points-leaders", "weekly-camp-points",
        "weekly-practice-by-instrument", "team-weekly-practice",
        "team-weekly-activity-points", "team-weekly-average-practice",
        "team-lifetime-practice", "team-practice-rating",
    ):
        opening = re.search(
            rf'<details class="contest-category-card"[^>]*data-contest-category="{category}"[^>]*>',
            rendered,
        )
        assert opening is not None
        assert " open" not in opening.group(0)
    assert rendered.count("Pristine") >= 4
    assert "MVP" not in rendered
    assert "Under construction" not in rendered
    division_layout = CSS[
        CSS.index(".contest-category-divisions {"):
        CSS.index(".contest-division-card,")
    ]
    assert "grid-template-columns: minmax(0, 1fr)" in division_layout
    assert "repeat(4" not in division_layout
