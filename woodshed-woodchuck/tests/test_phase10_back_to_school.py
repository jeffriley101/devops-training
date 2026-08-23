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


def test_streamers_burrow_stat_and_bonus_challenge_contract() -> None:
    assert 'class="back-to-school-streamers" aria-hidden="true"' in BOARD
    assert ".back-to-school-streamers span" in CSS
    assert "Highest Burrow Score to Date" in BOARD
    assert 'id="board-player-burrow-best">0</strong>' in BOARD
    assert '"woodshed.plungeBurrow.bestScore"' in APP

    bonus = BOARD[BOARD.index('class="board-practice-section bonus-challenge-section"'):]
    assert "🏆</span> Bonus Challenge" in bonus
    assert 'id="quest-text"' in bonus
    assert 'id="complete-quest-btn"' in bonus
    assert "I Played It" in bonus
    assert "board-locker" not in bonus


def test_hall_ui_separates_history_and_groups_achievements() -> None:
    assert "Historical winners" in BOARD
    assert "All-time history" in BOARD
    assert "grouped by season, category, and division" in BOARD
    hall = APP[APP.index("function wireHallOfChampions"):APP.index("function wirePersonalCrownProgress")]
    assert 'achievementList.className = "champion-achievements"' in hall
    assert "achievement.season.name" in hall
    assert "achievement.contest.name" in hall
    assert 'achievement.division === "open" ? "Open" : "Verified"' in hall
