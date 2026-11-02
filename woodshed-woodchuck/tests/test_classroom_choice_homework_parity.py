"""Golden parity against accepted 7ef3583 across every current Homework recipe."""
from datetime import date
from types import SimpleNamespace
import hashlib
import json
from app.weekly_homework import homework_spec, INSTRUMENT_DEFINITIONS, GOAL_OPTIONS


def test_personal_homework_matches_accepted_catalog():
    values=[]
    for instrument in (i['key'] for i in INSTRUMENT_DEFINITIONS):
        for focus in GOAL_OPTIONS:
            for mode in ('Beginner','Challenge','Performance'):
                for day in (date(2026,11,2),date(2026,11,9)):
                    for days in (0,5):
                        plan=SimpleNamespace(week_start=day,focus=focus,issued_game_mode=mode,
                            recent_results={'weeks':[{'logging_days':days}]},homework_minutes_target=60)
                        values.append(homework_spec(plan,instrument))
    assert hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest() == '61ef7a33213879ac653fd1d8da0795b7b81a854770596c8730febcb0f560a616'
