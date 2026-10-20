"""Frozen V1 musical material and support; no database, rewards, or rating inputs."""
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

import pytest

from app.content import GOAL_OPTIONS
from app.weekly_homework import homework_spec, weekly_instrument


def issued(mode="Challenge", focus=GOAL_OPTIONS[0], *, days=4, week=date(1970, 1, 5)):
    return SimpleNamespace(week_start=week, focus=focus, issued_game_mode=mode,
        homework_minutes_target={"Beginner": 10, "Challenge": 15, "Performance": 20}[mode],
        recent_results={"weeks": [{"logging_days": days // 2}, {"logging_days": days - days // 2}]})


@pytest.mark.parametrize("mode", ["Beginner", "Challenge", "Performance"])
@pytest.mark.parametrize("focus,phrase", list(zip(GOAL_OPTIONS, ["repeatable", "steady tone", "short segments", "vary its rhythm"])))
def test_focus_and_issued_mode_have_concrete_work(mode, focus, phrase):
    plan = issued(mode, focus)
    before = deepcopy(vars(plan))
    spec = homework_spec(plan, "Flute")
    assert phrase in " ".join(spec["instructions"]).lower()
    assert spec["presentation"] == {"Beginner": "numbered-steps", "Challenge": "repetition-metronome",
                                    "Performance": "continuous-attempt"}[mode]
    blocks = spec["suggested_blocks"]
    assert blocks["count"] * blocks["minutes_each"] == plan.homework_minutes_target
    assert set(spec) == {"schema_version", "recipe_id", "title", "instructions", "presentation", "suggested_blocks", "support"}
    assert spec["schema_version"] == "weekly_homework_v1"
    assert vars(plan) == before
    if focus == GOAL_OPTIONS[0]:
        print("Persistable specification", mode, json.dumps(spec), "parent minutes", plan.homework_minutes_target)


@pytest.mark.parametrize("instrument,a,b", [
    ("Piano / Keyboard", "both hands", "alternating scale"),
    ("Flute", "a trill", "trill at metronome speeds"),
    ("Clarinet", "crossing the break", "cross-the-break work at metronome speeds"),
    ("Saxophone", "middle-B tonguing", "tonguing at different speeds"),
    ("Trumpet", "low-F# tonguing", "tonguing at different speeds"),
    ("Trombone", "first-position lip slurs", "lip slurs with a metronome"),
    ("Tuba", "sustaining an easy note three times", "tuner consistency"),
    ("Percussion", "ten paradiddles", "even paradiddles with a metronome"),
])
def test_recipe_rotation_is_week_based(instrument, a, b):
    first = homework_spec(issued(), instrument)
    second = homework_spec(issued(week=date(1970, 1, 12)), instrument)
    again = homework_spec(issued(week=date(1970, 1, 19)), instrument)
    assert a in first["instructions"][0]
    assert b in second["instructions"][0]
    assert first["recipe_id"].endswith(":a") and second["recipe_id"].endswith(":b")
    assert first == again == homework_spec(issued(), instrument)


@pytest.mark.parametrize("alias,key", [("Piano", "piano-keyboard"), ("Keyboard", "piano-keyboard"),
    ("piano-keyboard", "piano-keyboard"), (" FLUTE ", "flute"), ("Hand Percussion", "percussion"),
    ("French Horn", "french-horn")])
def test_existing_aliases_are_canonical(alias, key):
    assert weekly_instrument(alias) == key
    assert homework_spec(issued(), alias) == homework_spec(issued(), key)


@pytest.mark.parametrize("instrument", ["Oboe", "Bassoon", "French Horn", "Baritone", "Violin", "Guitar", "Banjo", "Vocals"])
def test_other_supported_instruments_use_fixed_passage(instrument):
    first = homework_spec(issued(), instrument)
    assert "difficult passage slowly and evenly" in first["instructions"][0]
    assert first == homework_spec(issued(week=date(1970, 1, 12)), instrument)


@pytest.mark.parametrize("instrument", [None, "", "unknown", "Accordion", "Drum Major", "Color Guard"])
def test_unknown_and_retired_only_choices_are_not_guessed(instrument):
    with pytest.raises(ValueError):
        weekly_instrument(instrument)


@pytest.mark.parametrize("focus", [GOAL_OPTIONS[1], GOAL_OPTIONS[3]])
def test_percussion_uses_stroke_or_rhythm_not_pitched_notes(focus):
    spec = homework_spec(issued(focus=focus), "Percussion")
    text = " ".join(spec["instructions"])
    assert ("stroke and pulse" if focus == GOAL_OPTIONS[1] else "rhythm phrase") in text
    assert "comfortable notes" not in text and "steady tone" not in text


@pytest.mark.parametrize("mode,ordinary,shorter", [
    ("Beginner", (5, 2), (5, 2)), ("Challenge", (3, 5), (5, 3)), ("Performance", (2, 10), (4, 5)),
])
def test_recent_support_only_changes_blocks(mode, ordinary, shorter):
    plan = issued(mode, days=4)
    a = homework_spec(plan, "Tuba")
    plan.recent_results = issued(mode, days=3).recent_results
    b = homework_spec(plan, "Tuba")
    assert a["suggested_blocks"] == dict(zip(("count", "minutes_each"), ordinary))
    assert b["suggested_blocks"] == dict(zip(("count", "minutes_each"), shorter))
    assert a["support"] == "ordinary-blocks" and b["support"] == "shorter-blocks"
    assert {k: v for k, v in a.items() if k not in ("support", "suggested_blocks")} == {
        k: v for k, v in b.items() if k not in ("support", "suggested_blocks")}
    assert plan.issued_game_mode == mode
    assert plan.homework_minutes_target == {"Beginner": 10, "Challenge": 15, "Performance": 20}[mode]


def test_slot_current_preferences_rating_and_free_text_are_not_inputs():
    plan = issued()
    expected = homework_spec(plan, "Flute")
    plan.slot_no = 2
    plan.game_mode = "Performance"
    plan.first_choice_instrument = "Tuba"
    plan.recent_results.update(rating=999, notes="PRIVATE", completion=True)
    plan.recent_results["weeks"][0].update(rating=999, notes="PRIVATE", practice_seconds=999999)
    assert homework_spec(plan, "Flute") == expected
    assert "PRIVATE" not in json.dumps(expected)
