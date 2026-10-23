"""Real startup switch, fresh synthetic processes, and unchanged HTTP safeguards."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SWITCH = "WOODSHED_WEEKLY_LOOP2_ENABLED"
WEEKLY = {
    "weekly_newsletter", "weekly_plans", "weekly_pantry", "weekly_wheel",
    "weekly_monday_bonus", "weekly_pants",
}


def process(tmp_path, value, code):
    # No inherited database URLs, service credentials, or production settings.
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'activation.db'}",
        "SESSION_SECRET": "weekly-activation-synthetic-test-only",
        "SESSION_COOKIE_SECURE": "false",
        "APP_ENV": "test",
    }
    if value is not None:
        env[SWITCH] = value
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("value", [None, "", "false", "true", "True", "TRUE",
                                   "1", "yes", "on", " true", "true ", "true\n", "invalid"])
def test_strict_startup_switch_and_unchanged_other_gates(tmp_path, value):
    catalog = json.loads(process(tmp_path, value, """
import json
from app.feature_access import FEATURES
print(json.dumps({key: [feature.enabled, feature.required_access]
                  for key, feature in FEATURES.items()}))
"""))
    assert {key: catalog[key] for key in WEEKLY} == {
        key: [value == "true", "open"] for key in WEEKLY
    }
    assert {key: feature for key, feature in catalog.items() if key not in WEEKLY} == {
        "practice_insights": [True, "full"],
        **{key: [False, "full"] for key in (
            "advanced_practice_analytics", "practice_history_tools",
            "customization_collections", "bonus_game_content",
            "advanced_exercises", "seasonal_side_activities",
        )},
    }


@pytest.mark.parametrize("value", [None, "false", "true"])
def test_routes_and_rewards_with_actual_startup_switch(tmp_path, value):
    process(tmp_path, value, """
from tests.test_weekly2_activation import exercise_routes
exercise_routes()
""")
    if value == "true":
        # Restart against the same synthetic saved data to prove reversible gating.
        for setting in ("false", "true"):
            process(tmp_path, setting, """
from tests.test_weekly2_activation import exercise_restart
exercise_restart()
""")


def exercise_restart():
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from sqlalchemy import MetaData, select
    from app import weekly, login_streaks
    from app.db import engine
    from app.main import app

    assert engine.dialect.name == "sqlite" and Path(engine.url.database).is_file()
    metadata = MetaData()
    metadata.reflect(engine)

    def saved():
        with engine.connect() as connection:
            return {name: connection.execute(select(table).order_by(*table.primary_key.columns)).all()
                    for name, table in metadata.tables.items()}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 10, 19, 18, 31, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    before = saved()
    with pytest.MonkeyPatch.context() as patch:
        for module in (weekly, login_streaks):
            patch.setattr(module, "datetime", Clock)
        with TestClient(app, headers={"Origin": "http://testserver"}) as client:
            response = client.post("/account/login", data={"woodchuck_id": "WC-ACT-1", "pin": "2468"})
            assert response.status_code == 200, response.text
            status = 200 if os.environ[SWITCH] == "true" else 403
            for path in ("/weekly/plan", "/weekly/newsletter", "/weekly/claims",
                         "/weekly/pantry", "/weekly/wheel"):
                assert client.get(path).status_code == status
            for path in ("/home", "/store", "/family/practice"):
                assert client.get(path).status_code == 200
            assert client.post("/account/login-streak").status_code == 200
    assert saved() == before  # Includes practice, plans, receipts, wallet and contests.
    engine.dispose()


def exercise_routes():
    """Run only in the disposable subprocess; never override feature gates."""
    from datetime import datetime, timedelta, timezone
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import MetaData, select
    from app import (models, weekly, weekly_routes, weekly_plans, weekly_caper,
                     weekly_rewards, weekly_wheel, login_streaks,
                     practice_charts, practice_chart_routes)
    from app.age_privacy import declare_age
    from app.db import SessionLocal, engine
    from app.feature_access import FEATURES, can_use_feature
    from app.main import app
    from app.security import hash_pin
    from tests.test_weekly2_newsletter import assert_safe

    enabled = os.environ.get(SWITCH) == "true"
    assert all(FEATURES[key].enabled is enabled for key in WEEKLY)
    target = Path(engine.url.database)
    assert engine.dialect.name == "sqlite" and target.is_absolute() and not target.exists()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    metadata = MetaData()
    metadata.reflect(engine)

    def snapshot():
        with engine.connect() as connection:
            return {name: connection.execute(select(table).order_by(*table.primary_key.columns)).all()
                    for name, table in metadata.tables.items()}

    clock = [datetime(2026, 10, 19, 13, 30, tzinfo=weekly.CENTRAL)]
    real_utc = weekly.utc_instant

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = clock[0].astimezone(timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    with pytest.MonkeyPatch.context() as patch:
        # Control only time and the Wheel draw, preserving native auth and writers.
        for module in (weekly, weekly_routes, weekly_plans, weekly_caper,
                       weekly_rewards, weekly_wheel):
            patch.setattr(module, "utc_instant", lambda value=None: real_utc(value or clock[0]))
        for module in (models, practice_charts, practice_chart_routes, login_streaks):
            patch.setattr(module, "datetime", Clock)
        patch.setattr(weekly_wheel.secrets, "randbelow", lambda n: 2950)

        with SessionLocal.begin() as session:
            for pid, age in ((1, "13to17"), (2, "adult"), (3, "under13")):
                session.add(models.WoodchuckProfile(
                    id=pid, woodchuck_id=f"WC-ACT-{pid}", display_name=f"PRIVATE {pid}",
                    pin_hash=hash_pin("2468"), instrument="Flute", level="Advanced",
                    game_mode="Beginner", goal="Existing goal"))
                session.flush()
                declare_age(session, pid, age)
                session.add(models.WoodchuckState(profile_id=pid, revision=1,
                    state_json={"progress": {"credits": 40}}))
            # Six historical, timely synthetic practice dates; the seventh uses HTTP.
            for offset in range(1, 7):
                day = clock[0] - timedelta(days=offset)
                session.add(models.PracticeChart(profile_id=1, practice_date=day.date(),
                    minutes=5, source="p-book", instrument="Flute", created_at=real_utc(day),
                    include_contests=False, include_team_contests=False))

        def client(pid=None):
            result = TestClient(app, headers={"Origin": "http://testserver"})
            if pid is not None:
                response = result.post("/account/login", data={
                    "woodchuck_id": f"WC-ACT-{pid}", "pin": "2468"})
                assert response.status_code == 200, response.text
            return result

        reads = ("/weekly/newsletter", "/weekly/plan", "/weekly/claims",
                 "/weekly/pantry", "/weekly/wheel")
        writes = (
            ("/weekly/plan", {"focus": "Build daily consistency"}),
            ("/weekly/plan/instruments/current/start", {}),
            ("/weekly/plans/1/tasks/homework/claim", {}),
            ("/weekly/plans/1/clue/claim", {}),
            ("/weekly/clover/claim", {"request_key": "activation-clover"}),
            ("/weekly/wheel/plays", {"request_key": "activation-wheel"}),
            ("/weekly/newsletter/presentation", {"expected_week": "2026-10-19"}),
        )
        c, other, anonymous = client(1), client(2), client()
        before = snapshot()
        for path in reads:
            assert anonymous.get(path).status_code == 401
            assert c.get(path, headers={"x-woodshed-account": "WC-ACT-2"}).status_code == 401
        for path, body in writes:
            assert anonymous.post(path, json=body).status_code == 401
            assert c.post(path, json=body, headers={"Origin": "https://foreign.invalid"}).status_code == 403
        assert snapshot() == before

        with SessionLocal() as session:
            assert all(can_use_feature(session, 1, key) is enabled for key in WEEKLY)
            assert all(not can_use_feature(session, 999, key) for key in WEEKLY)

        if enabled:
            for path in reads:
                response = c.get(path)
                assert response.status_code == 200, response.text
                assert_safe(response.json())
            assert snapshot() == before  # Reads do not settle rewards or finalize contests.
            plan = c.post("/weekly/plan", json={"focus": "Build daily consistency"})
            assert plan.status_code == 200, plan.text
            pid = plan.json()["plan"]["id"]
            saved = snapshot()
            assert c.post("/weekly/plan", json={"focus": "Build daily consistency"}).json() == plan.json()
            assert snapshot() == saved
            body = {"start_context": plan.json()["start_context"]}
            assignment = c.post("/weekly/plan/instruments/current/start", json=body)
            assert assignment.status_code == 200, assignment.text
            assert assignment.json()["assignment"]["homework"]
            saved = snapshot()
            assert c.post("/weekly/plan/instruments/current/start", json=body).json() == assignment.json()
            assert snapshot() == saved
        else:
            for path in reads:
                assert c.get(path).status_code == 403
            for path, body in writes:
                assert c.post(path, json=body).status_code == 403
            assert snapshot() == before

        clock[0] += timedelta(minutes=1)
        body = {"practice_date": clock[0].date().isoformat(), "minutes": 30,
                "submission_key": "activation-practice", "note": "PRIVATE practice",
                "include_contests": False, "include_team_contests": False}
        practice = c.post("/practice-charts", json=body)
        assert practice.status_code == 201 and practice.json()["created"], practice.text
        saved = snapshot()
        retry = c.post("/practice-charts", json=body)
        assert retry.status_code == 201 and not retry.json()["created"], retry.text
        assert retry.json()["chart"]["id"] == practice.json()["chart"]["id"]
        assert c.post("/account/login-streak").status_code == 200
        assert snapshot() == saved
        assert c.get("/practice-charts/totals").json()["career_seconds"] == 3600
        with SessionLocal() as session:
            receipts = session.scalars(select(models.RewardGrant).where(
                models.RewardGrant.source_key == "first-seven-practice-dates")).all()
            assert [(r.profile_id, r.reward_type, r.amount) for r in receipts] == (
                [(1, "dandelion", 300)] if enabled else [])

        for path in ("/home", "/store", "/family/practice"):
            assert c.get(path).status_code == 200
        saved = snapshot()
        for path in ("/home", "/store", "/family/practice"):
            assert c.get(path).status_code == 200
        assert snapshot() == saved
        with SessionLocal() as session:
            receipts = session.scalars(select(models.RewardGrant).where(
                models.RewardGrant.source_key.startswith("weekly-monday:"))).all()
            assert [(r.profile_id, r.amount) for r in receipts] == ([(1, 75)] if enabled else [])

        if enabled:
            claim_path = f"/weekly/plans/{pid}/tasks/homework/claim"
            before = snapshot()
            assert other.post(claim_path, json={}).status_code == 404
            assert snapshot() == before
            claim = c.post(claim_path, json={})
            assert claim.status_code == 200 and claim.json()["claimed"], claim.text
            saved = snapshot()
            assert c.post(claim_path, json={}).json() == claim.json()
            assert snapshot() == saved
            assert c.get("/weekly/pantry").json()["wallet"]["pickled_carrot"] == 1

            # Explicit synthetic historical funding, not a claim of earning seven clues here.
            with SessionLocal.begin() as session:
                weekly_rewards.grant(session, 1, "synthetic-jars", "pickled_carrot", 2, now=clock[0])
                weekly_rewards.grant(session, 1, "synthetic-clues", "caper_clue", 7, now=clock[0])
            for path, body in (("/weekly/clover/claim", {"request_key": "activation-clover"}),
                               ("/weekly/wheel/plays", {"request_key": "activation-wheel"})):
                first = c.post(path, json=body)
                assert first.status_code == 200, first.text
                saved = snapshot()
                assert c.post(path, json=body).json() == first.json()
                assert snapshot() == saved
            assert c.get("/weekly/pantry").json()["wallet"]["pickled_carrot"] == 0
            presentation = {"expected_week": "2026-10-19"}
            assert c.post("/weekly/newsletter/presentation", json=presentation).json()["status"] == "reserved"
            saved = snapshot()
            assert c.post("/weekly/newsletter/presentation", json=presentation).json()["status"] == "already_reserved"
            assert other.post("/weekly/newsletter/presentation", json=presentation).status_code == 403
            assert snapshot() == saved

        # Native under-13 authorization still rejects access without parental consent.
        child = client(3)
        before = snapshot()
        for path in reads:
            assert child.get(path).status_code in (401, 403)
        for path, body in writes:
            assert child.post(path, json=body).status_code in (401, 403)
        assert snapshot() == before
        # All saved practice and contest history survive the feature and retry checks.
        final = snapshot()
        assert len(final["practice_charts"]) == 7
        for table in ("contest_weeks", "contest_results", "weekly_rating_events"):
            if table in final:
                assert final[table] == before[table] == []
        if not enabled:
            assert all(final[table] == [] for table in (
                "weekly_plans", "weekly_instrument_plans", "weekly_task_claims",
                "weekly_wheel_plays", "weekly_newsletter_presentations"))
            assert not any(row.source_key.startswith(("weekly-", "clover-caper:", "first-seven-"))
                           for row in final["reward_grants"])
        for connection in (c, other, anonymous, child):
            connection.close()
    engine.dispose()
