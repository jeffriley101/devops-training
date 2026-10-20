"""Accepted B3a contracts on current migrated SQLite/PostgreSQL and real HTTP authority."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app import arcade_access, arcade_rewards, arcade_routes, weekly_plans as plans, weekly_routes
from app.feature_access import FEATURES, Feature
from app.memberships import Actor, create_complimentary_membership
from app.models import ArcadeAttemptPack, ArcadeHighScore, ArcadePlaySession, WoodchuckProfile, WoodchuckState
from app.weekly import CENTRAL, utc_instant
from tests.test_weekly2_plans import FOCI, NOW, db, runtime, config, rows
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
import sys


@pytest.fixture
def plan_db(runtime):
    engine, factory, client = runtime
    factory.engine, factory.client = engine, client
    return factory


def protected_rows(factory):
    # Arcade's legitimate play/PB ledger writes are separate from Weekly reads.
    return {name: value for name, value in rows(factory.engine).items()
            if not name.startswith('arcade_')}


@pytest.fixture
def arcade_factory(db, monkeypatch):
    command.upgrade(config(), 'w25weekly003')
    from app.main import app
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    factory.engine = db
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    return factory


# Exact recursive confidentiality assertion retained from the accepted reference.
RATING_KEYS = {
    "rating", "delta", "rating_week_values", "rating_value", "rating_delta",
    "rating_history", "practice_rating", "score", "value", "history", "raw",
}

def assert_student_safe(value):
    if isinstance(value, dict):
        assert not RATING_KEYS.intersection(value), f"Exact rating fields exposed: {value}"
        for child in value.values():
            assert_student_safe(child)
    elif isinstance(value, list):
        for child in value:
            assert_student_safe(child)


NEXT_MONDAY = datetime(2026, 10, 19, tzinfo=CENTRAL)
ANSWERS = {"C Major": "E", "D Minor": "F", "E Minor": "G", "F Major": "A",
           "G Major": "B", "A Minor": "C", "B Minor": "D"}


def bind_arcade_clock(factory, monkeypatch):
    for module in (arcade_rewards, arcade_routes):
        monkeypatch.setattr(module, "_utc_now", lambda value=None: utc_instant(value or factory.clock[0]))


def issue(factory, mode="Challenge", *, at=NOW):
    with factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        profile.game_mode = mode
        session.commit()  # Issuance deliberately refreshes persisted profile authority.
        plan = plans.issue_current_plan(session, profile, FOCI[0], now=at)
        session.commit()
        return plan


def recorded_round(factory, *, score, start=NOW + timedelta(seconds=1),
                   end=NOW + timedelta(seconds=31), pid=1, game="thirds", authoritative=True):
    # Projection-only fixture: deliberately not offered as proof of actual gameplay.
    with factory() as session:
        row = ArcadePlaySession(profile_id=pid, game_key=game, play_token=uuid4().hex,
            started_at=utc_instant(start), completed_at=utc_instant(end) if end else None, submitted_score=score,
            authoritative_score=score if authoritative else None, entry_cost=0, payout=0)
        session.add(row)
        session.commit()
        return row.id


def progress(factory, plan, *, now=NOW + timedelta(minutes=2), pid=1):
    with factory() as session:
        saved = session.get(type(plan), plan.id)
        return plans.thirds_progress(session, pid, saved, now=now)


def all_rows(factory):
    with factory() as session:
        return {table: session.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all()
                for table in ("weekly_plans", "weekly_instrument_plans", "woodchuck_profiles",
                    "woodchuck_states", "arcade_play_sessions", "arcade_high_scores",
                    "arcade_attempt_packs", "arcade_start_requests", "reward_grants",
                    "quest_completions", "camp_point_awards")}


@pytest.mark.parametrize("mode,score,qualified", [
    ("Beginner", 2, False), ("Beginner", 3, True), ("Beginner", 8, True),
    ("Challenge", 0, False), ("Challenge", 6, False), ("Challenge", 7, True),
    ("Performance", 14, False), ("Performance", 15, True), ("Performance", 20, True),
    ("Challenge", 8, True),
])
def test_one_round_thresholds(plan_db, mode, score, qualified):
    plan = issue(plan_db, mode)
    recorded_round(plan_db, score=score)
    before = all_rows(plan_db)
    expected = {"target": {"Beginner": 3, "Challenge": 7, "Performance": 15}[mode],
        "qualified": qualified, "authoritative_score": score,
        "completion_status": "qualified" if qualified else "completed_below_target"}
    assert progress(plan_db, plan) == expected
    assert all_rows(plan_db) == before
    print("THIRDS_THRESHOLD", json.dumps(expected, sort_keys=True))


def test_max_not_sum_and_untrusted_or_foreign_results_never_count(plan_db):
    plan = issue(plan_db)
    assert progress(plan_db, plan) == {"target": 7, "qualified": False,
        "authoritative_score": None, "completion_status": "not_completed"}
    recorded_round(plan_db, score=4)
    recorded_round(plan_db, score=4)
    recorded_round(plan_db, score=999, authoritative=False)
    recorded_round(plan_db, score=999, end=None)
    recorded_round(plan_db, score=999, pid=2)
    recorded_round(plan_db, score=999, game="blue")
    with plan_db() as session:
        session.add(ArcadeHighScore(profile_id=1, game_key="thirds", best_score=9999))
        session.commit()
    assert progress(plan_db, plan)["authoritative_score"] == 4
    assert progress(plan_db, plan)["qualified"] is False
    recorded_round(plan_db, score=7)
    recorded_round(plan_db, score=6)
    assert progress(plan_db, plan) == {"target": 7, "qualified": True,
        "authoritative_score": 7, "completion_status": "qualified"}
    with pytest.raises(HTTPException) as denied:
        progress(plan_db, plan, pid=2)
    assert denied.value.status_code == 404


@pytest.mark.parametrize("start,end,now,qualifies", [
    (NOW - timedelta(seconds=1), NOW + timedelta(seconds=30), NEXT_MONDAY, False),
    (NOW, NOW + timedelta(seconds=30), NEXT_MONDAY, False),
    (NOW + timedelta(seconds=1), NOW + timedelta(seconds=31), NEXT_MONDAY, True),
    (NOW - timedelta(days=7), NOW - timedelta(days=7) + timedelta(seconds=30), NEXT_MONDAY, False),
    (NEXT_MONDAY + timedelta(seconds=1), NEXT_MONDAY + timedelta(seconds=31), NEXT_MONDAY + timedelta(minutes=1), False),
    (NEXT_MONDAY - timedelta(seconds=15), NEXT_MONDAY + timedelta(seconds=15), NEXT_MONDAY + timedelta(minutes=1), False),
    (NEXT_MONDAY - timedelta(seconds=31), NEXT_MONDAY - timedelta(microseconds=1), NEXT_MONDAY, True),
    (NEXT_MONDAY - timedelta(seconds=31), NEXT_MONDAY, NEXT_MONDAY, False),
    (NOW + timedelta(seconds=60), NOW + timedelta(seconds=30), NEXT_MONDAY, False),
    (NOW + timedelta(seconds=1), NOW + timedelta(seconds=31), NOW + timedelta(seconds=30), False),
], ids=["pre-plan", "equal-issuance", "after-issuance", "prior-week", "next-week",
        "sunday-monday", "last-instant", "next-monday-exact", "reversed-time", "future-completion"])
def test_recorded_time_boundaries(plan_db, start, end, now, qualifies):
    plan = issue(plan_db)
    recorded_round(plan_db, score=7, start=start, end=end)
    result = progress(plan_db, plan, now=now)
    assert result["qualified"] is qualifies
    assert result["authoritative_score"] == (7 if qualifies else None)
    if start < NEXT_MONDAY <= end:
        following = issue(plan_db, at=NEXT_MONDAY)
        assert progress(plan_db, following, now=now)["qualified"] is False


@pytest.mark.parametrize("monday,hours", [(datetime(2026, 3, 2, tzinfo=CENTRAL), 167),
                                         (datetime(2026, 10, 26, tzinfo=CENTRAL), 169)])
def test_chicago_calendar_week_handles_dst(plan_db, monday, hours):
    plan = issue(plan_db, at=monday)
    end = (monday + timedelta(days=7)).astimezone(timezone.utc)
    assert (end - monday.astimezone(timezone.utc)).total_seconds() == hours * 3600
    recorded_round(plan_db, score=7, start=end - timedelta(seconds=31),
                   end=end - timedelta(microseconds=1))
    assert progress(plan_db, plan, now=end)["qualified"] is True
    # A larger result at the next local Monday is excluded, even across DST.
    recorded_round(plan_db, score=15, start=end - timedelta(seconds=30), end=end)
    assert progress(plan_db, plan, now=end)["authoritative_score"] == 7


def test_free_rounds_have_no_pack_cap_or_payout_and_normal_games_stay_paid(plan_db, monkeypatch):
    assert arcade_access.ALWAYS_FREE == {"plunge-burrow", "blue", "thirds"}
    assert arcade_access.NORMAL == {"radio-tuner", "wheel-of-woodchuck", "scale-keyboard",
        "dressed-to-the-nines", "interval-basic-training", "history-mystery"}
    for gate in tuple(FEATURES):
        if gate.startswith("weekly_"):
            monkeypatch.setitem(FEATURES, gate, Feature(False, "open"))
    with plan_db() as session:
        create_complimentary_membership(session, Actor("student", 2), Actor("admin"))
        state = session.get(WoodchuckState, 1)
        data = deepcopy(state.state_json)
        data["progress"]["credits"] = 0
        state.state_json = data
        session.commit()
        for pid in (1, 2):
            assert arcade_access.access_policy(session, pid, "thirds") == {
                "classification": "always_free", "allowed": True,
                "free_reason": "always_free", "entry_cost": 0}
        for game in arcade_access.NORMAL:
            assert arcade_access.access_policy(session, 1, game)["entry_cost"] == 100
    before = protected_rows(plan_db)
    # Beyond both pack size (3) and generic reward count (10); no new daily cap.
    for n in range(11):
        with plan_db() as session:
            instant = NOW + timedelta(minutes=n)
            run = arcade_rewards.start_arcade_play(session, profile_id=1, game_key="thirds",
                                                   request_id=uuid4().hex, now=instant)
            assert run.play.entry_cost == 0 and run.play.pack_id is None
            result = arcade_rewards.complete_arcade_play(session, profile_id=1,
                play_token=run.play.play_token, score=0, now=instant + timedelta(seconds=30))
            assert result["payout"] == result["balance"] == 0
            assert not result["reward_eligible"]
            session.commit()
    with plan_db() as session:
        assert session.scalar(select(func.count()).select_from(ArcadeAttemptPack)) == 0
        assert session.scalar(select(func.count()).select_from(ArcadePlaySession)) == 11
        with pytest.raises(arcade_rewards.InsufficientArcadeBalanceError):
            arcade_rewards.start_arcade_play(session, profile_id=1, game_key="dressed-to-the-nines")
    assert protected_rows(plan_db) == before


def test_historical_paid_round_resumes_without_rewriting_or_consuming_pack(plan_db):
    with plan_db() as session:
        pack = ArcadeAttemptPack(profile_id=1, game_key="thirds", cost=100, attempts_used=1)
        session.add(pack)
        session.flush()
        play = ArcadePlaySession(profile_id=1, game_key="thirds", play_token=uuid4().hex,
            started_at=utc_instant(NOW), entry_cost=100, pack_id=pack.id, attempt_number=1)
        arcade_rewards.challenges.initialize(play)
        session.add(play)
        session.commit()
        original_id = play.id
    before = protected_rows(plan_db)
    with plan_db() as session:
        resumed = arcade_rewards.start_arcade_play(session, profile_id=1, game_key="thirds", now=NOW)
        assert resumed.resumed and resumed.play.id == original_id and resumed.play.entry_cost == 100
        arcade_rewards.complete_arcade_play(session, profile_id=1, play_token=resumed.play.play_token,
                                            score=0, now=NOW + timedelta(seconds=30))
        new = arcade_rewards.start_arcade_play(session, profile_id=1, game_key="thirds", now=NOW + timedelta(minutes=1))
        assert new.play.pack_id is None and new.play.entry_cost == 0
        assert session.scalar(select(ArcadeAttemptPack)).attempts_used == 1
        session.commit()
    assert protected_rows(plan_db) == before


def genuine_round(client, factory, score, *, forged=False):
    factory.clock[0] += timedelta(seconds=1)
    started = factory.clock[0]
    response = client.post("/arcade/plays", json={"game_key": "thirds", "request_id": uuid4().hex})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["free_reason"] == "always_free" and payload["charged_now"] == 0
    token, challenge = payload["play_token"], payload["challenge"]
    assert set(challenge["question"]) == {"chord"}
    if forged:
        factory.clock[0] += timedelta(seconds=.25)
        correct = ANSWERS[challenge["question"]["chord"]]
        result = client.post(f"/arcade/plays/{token}/action", json={
            "action_index": challenge["action_index"], "answer": "A" if correct != "A" else "B"})
        assert result.status_code == 200
        challenge = result.json()["challenge"]
        assert challenge["score"] == 0
    for _ in range(score):
        factory.clock[0] += timedelta(seconds=.25)
        result = client.post(f"/arcade/plays/{token}/action", json={
            "action_index": challenge["action_index"], "answer": ANSWERS[challenge["question"]["chord"]]})
        assert result.status_code == 200, result.text
        challenge = result.json()["challenge"]
    assert challenge["score"] == score
    if forged:
        # Achieving the target mid-round is not completion.
        assert client.get("/weekly/plan").json()["tasks"]["thirds"]["completion_status"] == "not_completed"
    factory.clock[0] = started + timedelta(seconds=31)
    if forged:
        rejected = client.post(f"/arcade/plays/{token}/complete", json={"score": 9999})
        assert rejected.status_code == 409
        with factory() as session:
            row = session.scalar(select(ArcadePlaySession).where(ArcadePlaySession.play_token == token))
            assert row.authoritative_score is None and row.completed_at is None
    result = client.post(f"/arcade/plays/{token}/complete", json={"score": score})
    assert result.status_code == 200, result.text
    assert result.json()["score"] == score and result.json()["result_authority"] == "server_scored"
    assert result.json()["payout"] == 0 and not result.json()["reward_eligible"]
    return token, result.json()


def test_real_route_score_weekly_status_personal_best_and_continued_play(plan_db, monkeypatch):
    bind_arcade_clock(plan_db, monkeypatch)
    issue(plan_db, "Beginner")
    with plan_db() as session:
        state = session.get(WoodchuckState, 1)
        data = deepcopy(state.state_json)
        data["progress"]["credits"] = 0
        state.state_json = data
        session.commit()
    with plan_db.client(1) as client:
        # Real login has its own existing daily grant, outside Arcade/Weekly reads.
        before = protected_rows(plan_db)
        token, result = genuine_round(client, plan_db, 5, forged=True)
        assert result["best_score"] == 5
        assert [(r["display_name"], r["score"]) for r in result["leaderboard"]] == [("Synthetic 1", 5)]
        status = client.get("/weekly/plan").json()
        assert status["tasks"]["thirds"] == {"target": 3, "qualified": True,
            "authoritative_score": 5, "completion_status": "qualified"}
        assert protected_rows(plan_db) == before
        sealed = all_rows(plan_db)
        assert client.post(f"/arcade/plays/{token}/complete", json={"score": 5}).json()["already_completed"]
        assert client.post(f"/arcade/plays/{token}/complete", json={"score": 6}).status_code == 409
        assert all_rows(plan_db) == sealed
        assert client.patch("/account/profile/game-mode", json={"game_mode": "Performance"}).status_code == 200
        before = protected_rows(plan_db)
        _, higher = genuine_round(client, plan_db, 12)
        assert higher["best_score"] == 12 and higher["leaderboard"][0]["score"] == 12
        after = client.get("/weekly/plan").json()
        assert after["plan"] == status["plan"]
        assert after["tasks"]["thirds"] == {"target": 3, "qualified": True,
            "authoritative_score": 12, "completion_status": "qualified"}
        assert protected_rows(plan_db) == before
        untouched = all_rows(plan_db)
        assert client.get("/weekly/plan").json() == after
        assert all_rows(plan_db) == untouched
        assert_student_safe(after)
        for private in ("recent_results", "play_token", "challenge_state", "reward_granted_at"):
            assert private not in json.dumps(after)
        print("THIRDS_REAL_ROUTE", json.dumps(after["tasks"], sort_keys=True))


def test_consented_child_can_qualify_privately(arcade_factory, monkeypatch):
    from app.main import app
    from app import tester_enrollments as testers, child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import (
        test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED,
    )
    from tests.test_arcade_economy import signed_client
    f = arcade_factory
    f.clock = [NOW]
    bind_arcade_clock(f, monkeypatch)
    for module in (plans, weekly_routes):
        monkeypatch.setattr(module, 'utc_instant', lambda value=None: utc_instant(value or f.clock[0]))
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(True, 'open'))
    monkeypatch.setattr(testers, 'clock', lambda: CLAIMED)
    # Real C002 enrollment, parent approval and cross-device child activation.
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(
        f, monkeypatch, '/prebeta/C002', 'C002', 'DIRECTOR1')
    with f() as session:
        p = session.scalar(select(WoodchuckProfile))
        pid, wid = p.id, p.woodchuck_id
    child = TestClient(app)
    assert child.post('/account/login', data={'woodchuck_id':wid,'pin':'2468'}).status_code == 200
    assert child.patch('/account/profile/game-mode', json={'game_mode':'Beginner'}).status_code == 200
    assert child.post('/weekly/plan', json={'focus':FOCI[0]}).status_code == 200
    other, _ = signed_client(f, 'OBSERVER')
    before = protected_rows(f)
    _, result = genuine_round(child, f, 3)
    assert result['best_score'] == 3 and result['leaderboard'] == []
    own = child.get('/weekly/plan').json()
    assert own['tasks']['thirds']['qualified'] is True
    assert_student_safe(own)
    assert child.get('/weekly/plan', params={'profile_id':pid+1}).status_code == 422
    assert other.get('/arcade/scores/thirds').json() == {'game_key':'thirds','best_score':0,'leaderboard':[]}
    assert other.get('/weekly/plan').json()['plan'] is None
    assert protected_rows(f) == before
    with f() as session:
        consent.withdraw_evidence(session, session.scalar(select(ConsentEvidence)))
        session.commit()
    after = rows(f.engine)
    cookie = child.cookies.get('session')
    assert child.get('/weekly/plan').status_code == 401
    assert child.post('/arcade/plays', json={'game_key':'thirds','request_id':uuid4().hex}).status_code == 401
    from tests.test_weekly2_game_mode import assert_only_session_retirement
    assert_only_session_retirement(after, rows(f.engine), cookie)
    assert other.get('/weekly/plan').status_code == 200
    child.close()
    other.close()


def test_lobby_uses_existing_always_free_presentation():
    root = Path(__file__).resolve().parents[1]
    html = (root / "templates/arcade.html").read_text()
    assert 'data-arcade-price="thirds">Always free</span>' in html
    assert 'data-arcade-price="dressed-to-the-nines">100 Dandelions · 3 attempts</span>' in html
    client = (root / "static/js/arcade-economy.js").read_text()
    assert 'payload.free_reason === "always_free"' in client and '? "Always free"' in client


@pytest.mark.parametrize('case', [
    'test_security_and_multi_account_public_top5',
    'test_wrong_answers_and_authoritative_scoring',
    'test_public_projection_privacy_ties_and_legacy',
    'test_resume_expiry_legacy_and_private_best',
    'test_completion_clock_tolerance_is_bounded',
])
def test_existing_thirds_authority_on_migrated_schema(arcade_factory, monkeypatch, case, record_property):
    from tests import test_arcade_public_authority as original
    record_property('original_test', 'tests/test_arcade_public_authority.py::'+case+'[thirds]')
    clock = original.clock.__wrapped__(monkeypatch)
    getattr(original, case)(arcade_factory, clock, 'thirds')


@pytest.mark.parametrize('case', [
    'test_exact_three_attempts_no_expiry_reload_retry_and_exhaustion',
    'test_insufficient_and_failed_transaction_leave_no_purchase',
    'test_http_mismatched_game_direct_score_and_replayed_completion',
    'test_classroom_policy_is_pluggable_but_no_fake_game_routes',
    'PILOT-D1', 'C001', 'membership',
])
def test_existing_paid_game_contracts_on_migrated_schema(arcade_factory, monkeypatch, case, record_property):
    from tests import test_release3_arcade as original
    from app.age_privacy import declare_age
    from app.security import hash_pin
    f = arcade_factory
    with f() as session:
        pin = hash_pin('2468')
        for pid in range(1, 9):
            session.add(WoodchuckProfile(id=pid, woodchuck_id=f'WC-R3-{pid}', display_name=f'Player {pid}',
                pin_hash=pin, instrument='Flute', level='Beginner', goal='Practice'))
            session.flush()
            declare_age(session, pid, 'adult', at=original.NOW-timedelta(days=10))
            session.add(WoodchuckState(profile_id=pid, revision=0, state_json={'progress':{'credits':100}}))
        session.commit()
    if case in ('PILOT-D1','C001','membership'):
        identity = 'test_real_full_entitlement_free_normal_games_but_not_classroom'
        original.test_real_full_entitlement_free_normal_games_but_not_classroom(f, case)
    else:
        identity = case
        args = [f, monkeypatch] if case in (
            'test_insufficient_and_failed_transaction_leave_no_purchase',
            'test_classroom_policy_is_pluggable_but_no_fake_game_routes') else [f]
        getattr(original, case)(*args)
    record_property('original_test', 'tests/test_release3_arcade.py::'+identity)


def test_existing_insufficient_paid_balance(arcade_factory, record_property):
    from tests.test_arcade_economy import test_insufficient_balance_rejects_start_without_negative_balance
    test_insufficient_balance_rejects_start_without_negative_balance(arcade_factory)
    record_property('original_test', 'tests/test_arcade_economy.py::test_insufficient_balance_rejects_start_without_negative_balance')


def test_repeated_qualification_preserves_populated_history(arcade_factory, monkeypatch):
    from tests.test_weekly2_foundation_migration import seed_history
    from app.age_privacy import declare_age
    f = arcade_factory
    seed_history(f.engine)  # Valid storage history, never used to infer permissions.
    with f() as session:
        declare_age(session, 1, 'adult', at=NOW-timedelta(days=1))
        session.commit()
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(True, 'open'))
    plan = issue(f, 'Challenge')
    with f() as session:
        p = session.get(WoodchuckProfile, 1)
        plans.start_current_instrument(session, p, plans.start_context(p, plan), now=NOW)
        session.commit()
    recorded_round(f, score=7)
    before = rows(f.engine)
    assert before['reward_grants'] and before['contest_results']
    assert before['team_week_membership_snapshots'] and before['classroom_reporting_periods']
    with f() as session:
        p = session.get(WoodchuckProfile, 1)
        saved = plans.load_current_plan(session, 1, now=NOW)
        for _ in range(3):
            payload = plans.current_plan_payload(session, p, saved, now=NOW+timedelta(minutes=2))
            assert payload['tasks']['thirds'] == {'target':7, 'qualified':True,
                'authoritative_score':7, 'completion_status':'qualified'}
            assert 'completion_status' not in payload['plan']['instruments'][0]
            assert payload['tasks']['homework']['qualified'] is False
        session.commit()
    assert rows(f.engine) == before
