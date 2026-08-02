from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app import contests
from app.models import (
    CampPointAward, ContestResult, CrownProgress, PracticeChart,
    PracticeChartVerification, QuestCompletion, RewardGrant,
)


ROOT = Path(__file__).resolve().parents[1]


def test_bonus_challenge_uses_configured_task_threshold_and_original_increment() -> None:
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    quest = app_js[app_js.index("function wireQuestForm"):app_js.index("const STORE_ITEMS")]
    assert "s.daily.questText" in quest
    assert "s.daily.targetMinutes" in quest
    assert "s.daily.loggedMinutes" in quest
    assert 'id="quest-text"' in (ROOT / "templates/quest.html").read_text()
    assert 'id="quest-target"' in (ROOT / "templates/quest.html").read_text()
    assert 'id="quest-progress"' in (ROOT / "templates/quest.html").read_text()
    assert "next.daily.loggedMinutes = payload.logged_minutes" in quest
    assert "payload.completed === true" in quest
    assert "stateApi.saveState(next, { sync: false });" in quest
    assert 'fetch("/contests/bonus-challenge/progress"' in quest
    assert 'fetch("/contests/quest/completions"' not in quest
    assert 'bonus-challenge/i-played-it' not in quest


def test_bonus_challenge_reward_is_threshold_only_and_not_daily_replacement() -> None:
    source = Path(contests.__file__).read_text(encoding="utf-8")
    route = source[source.index('@router.post("/bonus-challenge/progress")'):
                   source.index('@router.post("/quest/completions")')]
    assert 'reward_amount=5' in route
    assert 'points_awarded=2' in route
    assert 'team_id=None' in route
    assert 'completed = logged_minutes >= target_minutes' in route
    assert 'source_key = f"bonus-challenge:{resolved[\'instance_key\']}"' in route
    assert '@router.get("/bonus-challenge/i-played-it")' not in source
    assert '@router.post("/bonus-challenge/i-played-it")' not in source
    assert "I_PLAYED_IT_DANDELIONS" not in source
    assert source.count("resolve_current_bonus_challenge(") >= 3
    assert '@router.get("/bonus-challenge/current")' in source
    assert "submitted.challenge_instance != resolved[\"instance_key\"]" in route


def test_bonus_completion_model_has_no_chart_or_contest_side_effects() -> None:
    source = Path(contests.__file__).read_text(encoding="utf-8")
    route = source[source.index('def record_bonus_challenge_progress('):source.index('@router.post("/quest/completions")')]
    assert "QuestCompletion(" in route  # Existing Bonus Challenge completion record.
    for forbidden in (
        "PracticeChart(", "PracticeChartVerification(", "ContestResult(",
        "CrownProgress(", "send_email", "finalize", "participation",
    ):
        assert forbidden not in route


def assert_bonus_rows(session, profile_id: int) -> None:
    """Shared row-count assertions used by route integration tests."""
    assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 1
    assert session.scalar(select(func.count()).select_from(RewardGrant)) == 1
    assert session.scalar(select(func.count()).select_from(CampPointAward)) == 1
    assert session.scalar(select(func.count()).select_from(PracticeChart)) == 0
    assert session.scalar(select(func.count()).select_from(PracticeChartVerification)) == 0
    assert session.scalar(select(func.count()).select_from(ContestResult)) == 0
    assert session.scalar(select(func.count()).select_from(CrownProgress)) == 0
    award = session.scalar(select(CampPointAward).where(CampPointAward.profile_id == profile_id))
    assert award is not None and award.points_awarded == 2 and award.team_id is None
