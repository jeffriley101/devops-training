from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bonus_challenge_uses_configured_task_threshold_and_original_increment() -> None:
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    quest = app_js[app_js.index("function wireQuestForm"):app_js.index("const STORE_ITEMS")]
    assert "s.daily.questText" in quest
    assert "s.daily.targetMinutes" not in quest
    assert "s.daily.loggedMinutes" not in quest
    assert 'id="quest-text"' in (ROOT / "templates/quest.html").read_text()
    assert 'id="quest-target"' not in (ROOT / "templates/quest.html").read_text()
    assert 'id="quest-progress"' not in (ROOT / "templates/quest.html").read_text()
    assert 'id="practice-minutes"' not in (ROOT / "templates/quest.html").read_text()
    assert 'id="practice-note"' not in (ROOT / "templates/quest.html").read_text()
    assert "next.daily.loggedMinutes = payload.logged_minutes" in quest
    assert "payload.completed === true" in quest
    assert "stateApi.saveState(next, { sync: false });" in quest
    assert 'fetch("/contests/bonus-challenge/progress"' in quest
    post_body = quest[quest.index("body: JSON.stringify({"):quest.index("}),", quest.index("body: JSON.stringify({"))]
    assert "activity_date: dateKey" in post_body
    assert "challenge_instance: currentChallengeInstance" in post_body
    assert "minutes" not in post_body
    assert "note" not in post_body
    assert 'fetch("/contests/quest/completions"' not in quest
    assert 'bonus-challenge/i-played-it' not in quest


# The former source-string tests required unconditional completion/reward code.
# Behavioral coverage for both aliases now lives in test_sec003_authority.py:
# retries, invalid assignments and browser-state claims create no earning rows.
