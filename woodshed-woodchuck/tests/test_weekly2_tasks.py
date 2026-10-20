"""B3b-1 trusted progress, with synthetic records and real BOARD transactions."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import event, func, select, text

from app import contests, weekly_plans as plans, weekly_tasks as tasks
from app.models import CampPointAward, PracticeChart, QuestCompletion, RewardGrant, WoodchuckProfile, WoodchuckState
from app.weekly import CENTRAL, utc_instant
from app.weekly_models import WeeklyInstrumentPlan, WeeklyPlan
from app.xp import xp_sources
from tests.test_weekly2_thirds import assert_student_safe, arcade_factory
from tests.test_weekly2_plans import FOCI, NOW, db, runtime, config, rows as database_rows
from tests.test_weekly2_game_mode import assert_only_session_retirement
from tests.test_weekly2_plans import observe_block
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from alembic import command
import sys
from threading import Event
from concurrent.futures import ThreadPoolExecutor


@pytest.fixture
def plan_db(runtime):
    engine, factory, client = runtime
    factory.engine, factory.client = engine, client
    # Legitimate login has its own existing reward; establish it before test baselines.
    for pid in (1, 2):
        with client(pid) as authenticated:
            assert authenticated.get('/account/me').status_code == 200
    with factory() as session:
        factory.initial_rewards = session.execute(text('SELECT * FROM reward_grants ORDER BY 1')).all()
        factory.initial_revision = session.get(WoodchuckState, 1).revision
    return factory


END = datetime(2026, 10, 19, tzinfo=CENTRAL)
ROUTE = "/contests/bonus-challenge/progress"


@pytest.fixture
def task_db(plan_db, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = utc_instant(plan_db.clock[0])
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(contests, "datetime", Clock)
    return plan_db


def issue(factory, mode="Challenge"):
    with factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        profile.game_mode = mode
        session.commit()
        row = plans.issue_current_plan(session, profile, FOCI[0], now=factory.clock[0])
        session.commit()
        return row


def assign(factory, plan, instrument="Flute", *, started=NOW):
    with factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        profile.instrument = instrument
        session.commit()
        result = plans.start_current_instrument(session, profile, plans.start_context(profile, plan), now=started)
        session.commit()
        return session.get(WeeklyInstrumentPlan, (plan.id, result["slot_no"]))


def chart(factory, *, practiced=NOW.date(), submitted=NOW + timedelta(minutes=1),
          minutes=9, seconds=None, instrument="Flute", pid=1, source="p-book"):
    with factory() as session:
        session.add(PracticeChart(profile_id=pid, practice_date=practiced,
            created_at=utc_instant(submitted), instrument=instrument, source=source,
            minutes=minutes, detected_playing_seconds=seconds,
            note="PRIVATE CHART NOTE", practice_details=["PRIVATE DETAIL"],
            include_contests=False, include_team_contests=False))
        session.commit()


def progress(factory, plan, *, now=END):
    with factory() as session:
        return tasks.homework_progress(session, 1, plan, now=now)


def board(factory, plan, *, now=END):
    with factory() as session:
        return tasks.board_progress(session, 1, plan, now=now)


def rows(factory):
    with factory() as session:
        return {name: session.execute(text(f"SELECT * FROM {name} ORDER BY 1")).all()
                for name in ("weekly_plans", "weekly_instrument_plans", "woodchuck_profiles",
                    "woodchuck_states", "practice_charts", "reward_grants", "quest_completions",
                    "camp_point_awards", "crown_awards", "crown_progress")}


def current(client):
    response = client.get("/contests/bonus-challenge/current")
    assert response.status_code == 200, response.text
    value = response.json()["challenge"]
    return {"activity_date": value["activity_date"], "challenge_instance": value["instance_key"]}


@pytest.mark.parametrize("mode,target", [("Beginner", 600), ("Challenge", 900), ("Performance", 1200)])
def test_homework_frozen_targets_exact_seconds_and_additive_credit(task_db, mode, target):
    plan = issue(task_db, mode)
    assign(task_db, plan)
    chart(task_db, minutes=(target // 60) - 1)
    chart(task_db, minutes=1, seconds=59, source="pristine")
    result = progress(task_db, plan)
    assert result["target_seconds"] == target
    assert result["assignments"][0]["practice_seconds"] == target - 1
    assert result["qualified"] is False
    chart(task_db, minutes=1, seconds=1, source="pristine")
    with task_db() as session:
        session.get(WoodchuckProfile, 1).game_mode = "Beginner" if mode != "Beginner" else "Performance"
        session.commit()
    before = rows(task_db)
    result = progress(task_db, plan)
    assert result["assignments"][0]["practice_seconds"] == target
    assert result["target_seconds"] == target and result["qualified"] is True
    with task_db() as session:
        assert plans.current_general_progress(session, 1, now=END, week_start=plan.week_start)["practice_seconds"] == target
    assert rows(task_db) == before
    assert before["reward_grants"] == task_db.initial_rewards
    assert before["quest_completions"] == []
    print("HOMEWORK_TARGET", json.dumps(result))


@pytest.mark.parametrize("practiced,submitted,now,expected", [
    (date(2026, 10, 11), NOW + timedelta(minutes=1), END, 0),
    (date(2026, 10, 12), NOW - timedelta(microseconds=1), END, 0),
    (date(2026, 10, 12), NOW, END, 0),
    (date(2026, 10, 12), NOW + timedelta(microseconds=1), END, 540),
    (date(2026, 10, 13), NOW + timedelta(days=1), END, 540),
    (date(2026, 10, 18), END - timedelta(microseconds=1), END, 540),
    (date(2026, 10, 18), END, END + timedelta(days=1), 0),
    (date(2026, 10, 18), END + timedelta(seconds=1), END + timedelta(days=1), 0),
    (date(2026, 10, 19), END - timedelta(seconds=1), END + timedelta(days=1), 0),
    (date(2026, 10, 13), NOW + timedelta(minutes=1), NOW + timedelta(minutes=2), 0),
    (date(2026, 10, 12), NOW + timedelta(minutes=2), NOW + timedelta(minutes=1), 0),
], ids=["prior-practice-date", "submitted-before", "submitted-equal", "accepted-same-date-approximation",
        "next-day", "last-week-instant", "next-monday-exact", "late-after-week", "next-week-practice",
        "future-practice", "future-submission"])
def test_homework_assignment_and_week_boundaries(task_db, practiced, submitted, now, expected):
    plan = issue(task_db)
    assign(task_db, plan)
    chart(task_db, practiced=practiced, submitted=submitted)
    assert progress(task_db, plan, now=now)["assignments"][0]["practice_seconds"] == expected


def test_homework_prior_assignment_date_and_partial_instruments_never_combine(task_db):
    plan = issue(task_db)
    started = NOW + timedelta(days=1)
    assign(task_db, plan, "Flute", started=started)
    assign(task_db, plan, "Tuba", started=started)
    chart(task_db, practiced=NOW.date(), submitted=started + timedelta(minutes=1), minutes=100)
    for instrument in ("Flute", "Tuba"):
        chart(task_db, practiced=started.date(), submitted=started + timedelta(minutes=1), instrument=instrument)
    chart(task_db, practiced=started.date(), submitted=started + timedelta(minutes=1), instrument="Violin", minutes=100)
    chart(task_db, practiced=started.date(), submitted=started + timedelta(minutes=1), pid=2, minutes=100)
    chart(task_db, practiced=started.date(), submitted=started + timedelta(minutes=1), source="quest", minutes=100)
    result = progress(task_db, plan)
    assert [a["practice_seconds"] for a in result["assignments"]] == [540, 540]
    assert result["qualified"] is False
    chart(task_db, practiced=started.date(), submitted=started + timedelta(minutes=2), minutes=6)
    result = progress(task_db, plan)
    assert [a["practice_seconds"] for a in result["assignments"]] == [900, 540]
    assert [a["qualified"] for a in result["assignments"]] == [True, False]
    assert result["qualified"] is True
    chart(task_db, practiced=started.date(), submitted=started + timedelta(minutes=3), minutes=6, instrument="Tuba")
    assert progress(task_db, plan)["qualified"] is True
    assert rows(task_db)["reward_grants"] == task_db.initial_rewards
    print("INDEPENDENT_HOMEWORK", json.dumps(result))


def test_homework_canonical_alias_and_unrecognized_chart(task_db):
    plan = issue(task_db)
    assign(task_db, plan, "Piano/Keyboard")
    for instrument in ("Piano", "Keyboard", "Piano / Keyboard", "piano-keyboard"):
        chart(task_db, instrument=instrument, minutes=2)
    chart(task_db, instrument="Unknown Legacy", minutes=100)
    chart(task_db, instrument="Flute", minutes=100)
    assert progress(task_db, plan)["assignments"][0]["practice_seconds"] == 480


def test_homework_chicago_dst_week_end_is_not_168_hours(task_db):
    task_db.clock[0] = datetime(2026, 10, 26, 0, 0, tzinfo=CENTRAL)
    plan = issue(task_db)
    assign(task_db, plan, started=task_db.clock[0])
    end = datetime(2026, 11, 2, tzinfo=CENTRAL)
    chart(task_db, practiced=date(2026, 11, 1), submitted=end - timedelta(seconds=1), minutes=15)
    assert progress(task_db, plan, now=end)["qualified"] is True
    chart(task_db, practiced=date(2026, 11, 1), submitted=end, minutes=99)
    assert progress(task_db, plan, now=end + timedelta(days=1))["assignments"][0]["practice_seconds"] == 900


def test_real_board_two_dates_once_five_dandelions_and_zero_activity(task_db):
    with task_db() as session:
        session.get(WoodchuckProfile, 1).instrument = "Banjo"  # Same fallback recipe on both days.
        session.commit()
    plan = issue(task_db)
    task_db.clock[0] += timedelta(seconds=1)
    with task_db.client(1) as client:
        payload = current(client)
        first = client.post(ROUTE, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["dandelions_awarded"] == 5
        assert first.json()["camp_points_awarded"] == 0
        assert first.json()["credits"] == 29
        saved = rows(task_db)
        duplicate = client.post(ROUTE, json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json()["created"] is False and duplicate.json()["dandelions_awarded"] == 0
        assert rows(task_db) == saved
        assert client.get("/weekly/plan").json()["tasks"]["board"] == {"target": 2, "completed": 1, "qualified": False}
        task_db.clock[0] += timedelta(days=1)
        second = client.post(ROUTE, json=current(client))
        assert second.status_code == 200 and second.json()["credits"] == 34
        assert client.get("/weekly/plan").json()["tasks"]["board"] == {"target": 2, "completed": 2, "qualified": True}
        task_db.clock[0] += timedelta(days=1)
        assert client.post(ROUTE, json=current(client)).json()["credits"] == 39
    assert board(task_db, plan) == {"target": 2, "completed": 2, "qualified": True}
    with task_db() as session:
        assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 3
        assert len(set(session.scalars(select(QuestCompletion.quest_id)))) == 1
        grants = session.scalars(select(RewardGrant).where(RewardGrant.category_key.startswith(contests.BONUS_REWARD_CATEGORY))).all()
        assert len(grants) == 3 and all(g.amount == 5 and g.reward_type == "dandelion" for g in grants)
        assert all(g.category_key == "board-bonus-v1:banjo" for g in grants)
        assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0
        assert xp_sources(session, profile_id=1)["board_points"] == 0
        assert session.get(WoodchuckState, 1).revision == task_db.initial_revision + 3
        assert session.scalar(select(func.count()).select_from(PracticeChart)) == 0


def test_board_stale_forged_foreign_date_and_cross_instrument_daily_limit(task_db):
    with task_db() as session:
        session.get(WoodchuckProfile, 1).instrument = "Banjo"
        session.commit()
    with task_db.client(1) as client:
        payload = current(client)
        before = rows(task_db)
        for body, status in (({**payload, "challenge_instance": "forged"}, 409),
                             ({**payload, "profile_id": 2}, 422),
                             ({**payload, "completed": True}, 422),
                             ({**payload, "activity_date": "2026-10-11"}, 400),
                             ({**payload, "activity_date": "2026-10-13"}, 400)):
            assert client.post(ROUTE, json=body).status_code == status
        assert client.post(ROUTE, json=payload, headers={"x-woodshed-account": "WL2-PLAN-2"}).status_code == 401
        assert rows(task_db) == before
        assert client.post(ROUTE, json=payload).status_code == 200
        assert client.patch("/account/profile/instrument", json={"instrument": "Baritone"}).status_code == 200
        before = rows(task_db)
        # Same recipe id on another instrument cannot turn a used date into a new reward.
        assert client.post(ROUTE, json=payload).status_code == 409
        assert client.post(ROUTE, json=current(client)).status_code == 409
        assert rows(task_db) == before


@pytest.mark.parametrize("failure", ["completion-insert", "balance-update", "before-commit"])
def test_board_sql_and_commit_failures_leave_neither_reward_nor_completion(task_db, failure):
    with task_db.client(1) as client:
        payload = current(client)
        before = rows(task_db)
        def fail(*args):
            raise RuntimeError("synthetic " + failure)
        target, event_name = {"completion-insert": (QuestCompletion, "after_insert"),
                              "balance-update": (WoodchuckState, "after_update"),
                              "before-commit": (task_db.class_, "before_commit")}[failure]
        event.listen(target, event_name, fail)
        try:
            with pytest.raises(RuntimeError, match="synthetic " + failure):
                client.post(ROUTE, json=payload)
        finally:
            event.remove(target, event_name, fail)
        assert rows(task_db) == before


@pytest.mark.parametrize("when", ["pre-plan", "equal-issuance", "next-week"])
def test_real_board_success_outside_plan_cutoffs_never_qualifies(task_db, when):
    if when == "next-week":
        plan = issue(task_db)
        task_db.clock[0] = END
    elif when == "pre-plan":
        task_db.clock[0] = NOW - timedelta(seconds=1)
    with task_db.client(1) as client:
        assert client.post(ROUTE, json=current(client)).json()["dandelions_awarded"] == 5
    if when != "next-week":
        task_db.clock[0] = NOW
        plan = issue(task_db)
    assert board(task_db, plan, now=END + timedelta(days=1)) == {"target": 2, "completed": 0, "qualified": False}


@pytest.mark.parametrize("defect", ["no-grant", "old-category", "wrong-amount", "wrong-source", "wrong-time", "foreign-owner"])
def test_board_rows_require_matching_success_receipt(task_db, defect):
    plan = issue(task_db)
    at = utc_instant(NOW + timedelta(minutes=1))
    with task_db() as session:
        session.add(QuestCompletion(profile_id=1, activity_date=NOW.date(), quest_id="flute-trill",
            logged_minutes=10, reward_amount=5, completed_at=at, created_at=at))
        if defect != "no-grant":
            session.add(RewardGrant(profile_id=2 if defect == "foreign-owner" else 1,
                source_key="wrong" if defect == "wrong-source" else "bonus-challenge:2026-10-12:flute-trill",
                reward_type="dandelion", amount=20 if defect == "wrong-amount" else 5,
                category_key="legacy" if defect == "old-category" else "board-bonus-v1:flute",
                created_at=at + timedelta(seconds=1) if defect == "wrong-time" else at))
        session.commit()
    assert board(task_db, plan)["completed"] == 0


def test_tasks_payload_is_private_read_only_and_no_plan_fabricates_nothing(task_db):
    with task_db.client(1) as client:
        before = rows(task_db)
        assert client.get("/weekly/plan").json()["plan"] is None
        assert "tasks" not in client.get("/weekly/plan").json()
        assert rows(task_db) == before
        plan = issue(task_db)
        assign(task_db, plan, "Trumpet")
        chart(task_db, instrument="Trumpet")
        task_db.clock[0] += timedelta(minutes=2)
        before = rows(task_db)
        expected = {"homework": {"target_seconds": 900, "qualified": False, "assignments": [
            {"slot_no": 1, "instrument": "trumpet", "practice_seconds": 540,
             "target_seconds": 900, "qualified": False}]},
            "board": {"target": 2, "completed": 0, "qualified": False},
            "thirds": {"target": 7, "qualified": False, "authoritative_score": None,
                       "completion_status": "not_completed"}}
        for _ in range(2):
            response = client.get("/weekly/plan")
            assert response.status_code == 200
            assert response.json()["tasks"] == expected
            assert_student_safe(response.json())
            assert "PRIVATE" not in response.text and "recent_results" not in response.text
        assert rows(task_db) == before
        assert before["reward_grants"] == task_db.initial_rewards
        assert before["quest_completions"] == []
        with task_db() as session:
            with pytest.raises(HTTPException) as denied:
                tasks.homework_progress(session, 2, plan, now=END)
            assert denied.value.status_code == 404
            with pytest.raises(HTTPException) as denied:
                tasks.board_progress(session, 2, plan, now=END)
            assert denied.value.status_code == 404
        print("WEEKLY_TASKS_PAYLOAD", json.dumps(expected, sort_keys=True))


def test_board_reauthorizes_after_profile_writer_fence(task_db, monkeypatch):
    original = contests.current_profile
    calls = []
    def changed(request, session):
        profile = original(request, session)
        if request.method == "POST" and profile is not None and not calls:
            calls.append(profile.id)
            with task_db() as other:
                other.get(WoodchuckProfile, profile.id).session_version += 1
                other.commit()
        return profile
    with task_db.client(1) as client:
        payload = current(client)
        monkeypatch.setattr(contests, "current_profile", changed)
        assert client.post(ROUTE, json=payload).status_code == 401
    with task_db() as session:
        assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
        assert session.scalar(select(func.count()).select_from(RewardGrant)) == len(task_db.initial_rewards)
        assert session.get(WoodchuckState, 1).revision == task_db.initial_revision


def body_for(client, legacy=False):
    challenge = client.get('/contests/bonus-challenge/current').json()['challenge']
    body = {'activity_date': challenge['activity_date'], 'challenge_instance': challenge['instance_key']}
    if legacy:
        body.update(quest_id=challenge['challenge_id'], minutes=challenge['target_minutes'],
                    logged_minutes=challenge['target_minutes'])
    return body


def route_for(legacy):
    return '/contests/quest/completions' if legacy else ROUTE


@pytest.mark.parametrize('legacy', [False, True])
def test_bonus_first_party_scope_and_gate_independence(task_db, monkeypatch, legacy):
    from app.feature_access import FEATURES, Feature
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(False, 'open'))
    with task_db.client(1) as client:
        body = body_for(client, legacy)
        before = database_rows(task_db.engine)
        for headers in ({'Origin':'http://foreign.invalid'}, {'Origin':'http://testserver:9999'},
                        {'Origin':''}, {'Origin':'null'}, {'Sec-Fetch-Site':'same-site'}):
            assert client.post(route_for(legacy), json=body, headers=headers).status_code == 403
        request = client.build_request('POST', route_for(legacy), json=body)
        del request.headers['origin']
        assert client.send(request).status_code == 403
        for selector in ('profile_id', 'instrument', 'activity_date'):
            assert client.post(route_for(legacy), json=body, params={selector:'forged'}).status_code == 422
        for selector in ('profile_id', 'instrument', 'completed', 'reward_amount'):
            assert client.post(route_for(legacy), json={**body,selector:2}).status_code == 422
        assert client.post(route_for(legacy), json=body,
                           headers={'x-woodshed-account':'WL2-PLAN-2'}).status_code == 401
        assert database_rows(task_db.engine) == before
        assert client.get('/weekly/plan').status_code == 403
        assert client.get('/account/me').status_code == 200
        response = client.post(route_for(legacy), json=body)
        assert response.status_code == 200, response.text
        assert response.json()['created'] is True
        assert database_rows(task_db.engine)['practice_charts'] == before['practice_charts']


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('defect', ['missing-receipt', 'old-category', 'wrong-instrument',
                                  'wrong-time', 'wrong-amount', 'activity-v2', 'orphan-receipt'])
def test_historical_daily_receipts_never_upgrade_or_pay_again(task_db, legacy, defect):
    with task_db.client(1) as client:
        body = body_for(client, True)
        at = utc_instant(task_db.clock[0])
        key = f"bonus-challenge:{body['activity_date']}:{body['quest_id']}"
        with task_db() as session:
            if defect == 'activity-v2':
                resolved = contests.resolve_current_bonus_challenge(session,
                    profile=session.get(WoodchuckProfile, 1), now=at)
                session.add(CampPointAward(profile_id=1, activity_type='quest', points_awarded=2,
                    occurred_at=at, created_at=at, duplicate_key=contests._bonus_daily_key(at.astimezone(CENTRAL).date())))
                key = contests._bonus_reward_key(resolved)
            elif defect != 'orphan-receipt':
                session.add(QuestCompletion(profile_id=1, activity_date=at.astimezone(CENTRAL).date(),
                    quest_id=body['quest_id'], logged_minutes=body['minutes'], reward_amount=5,
                    completed_at=at, created_at=at))
            if defect != 'missing-receipt':
                session.add(RewardGrant(profile_id=1, source_key=key, reward_type='dandelion',
                    amount=20 if defect=='wrong-amount' else 5,
                    category_key='legacy' if defect in ('old-category','activity-v2') else
                        'board-bonus-v1:tuba' if defect=='wrong-instrument' else 'board-bonus-v1:flute',
                    created_at=at+timedelta(seconds=1) if defect=='wrong-time' else at))
            session.commit()
        before = database_rows(task_db.engine)
        submitted = body if legacy else {k:body[k] for k in ('activity_date','challenge_instance')}
        assert client.post(route_for(legacy), json=submitted).status_code == 409
        assert database_rows(task_db.engine) == before


@pytest.mark.parametrize('defect', ['empty-category', 'noncanonical-category', 'unknown-category',
                                  'wrong-reward-type', 'wrong-completion-amount', 'future'])
def test_board_receipt_projection_rejects_additional_mismatches(task_db, defect):
    plan = issue(task_db)
    at = utc_instant(NOW + timedelta(minutes=1))
    with task_db() as session:
        session.add(QuestCompletion(profile_id=1, activity_date=NOW.date(), quest_id='flute-trill',
            logged_minutes=10, reward_amount=20 if defect=='wrong-completion-amount' else 5,
            completed_at=at, created_at=at))
        session.add(RewardGrant(profile_id=1, source_key='bonus-challenge:2026-10-12:flute-trill',
            reward_type='xp' if defect=='wrong-reward-type' else 'dandelion', amount=5,
            category_key={'empty-category':'board-bonus-v1:', 'noncanonical-category':'board-bonus-v1:Flute',
                          'unknown-category':'board-bonus-v1:not-an-instrument'}.get(defect,'board-bonus-v1:flute'),
            created_at=at))
        session.commit()
    assert board(task_db, plan, now=NOW if defect=='future' else END)['completed'] == 0


@pytest.mark.parametrize('monday', [datetime(2026,3,2,tzinfo=CENTRAL), datetime(2026,10,26,tzinfo=CENTRAL)])
def test_homework_and_board_dst_issuance_and_end(task_db, monday):
    task_db.clock[0] = monday
    plan = issue(task_db)
    assign(task_db, plan, started=monday)
    end = monday + timedelta(days=7)
    chart(task_db, practiced=(end-timedelta(days=1)).date(), submitted=end-timedelta(microseconds=1), minutes=15)
    with task_db() as session:
        for day, instant in ((monday.date(), monday+timedelta(seconds=1)),
                             ((end-timedelta(days=1)).date(), end-timedelta(microseconds=1))):
            instant = utc_instant(instant)
            session.add(QuestCompletion(profile_id=1,activity_date=day,quest_id='same-recipe',
                logged_minutes=10,reward_amount=5,completed_at=instant,created_at=instant))
            session.add(RewardGrant(profile_id=1,source_key=f'bonus-challenge:{day}:same-recipe',
                reward_type='dandelion',amount=5,category_key='board-bonus-v1:flute',created_at=instant))
        session.commit()
    before = database_rows(task_db.engine)
    assert progress(task_db,plan,now=end)['qualified']
    assert board(task_db,plan,now=end)=={'target':2,'completed':2,'qualified':True}
    assert database_rows(task_db.engine)==before


def test_receipt_insert_rollback_lost_response_and_retry(task_db, monkeypatch):
    with task_db.client(1) as client:
        body = body_for(client)
        before = database_rows(task_db.engine)
        def fail_insert(*args):
            raise RuntimeError('synthetic receipt insert failure')
        event.listen(RewardGrant, 'after_insert', fail_insert)
        try:
            with pytest.raises(RuntimeError,match='synthetic receipt insert failure'):
                client.post(ROUTE,json=body)
        finally:
            event.remove(RewardGrant,'after_insert',fail_insert)
        assert database_rows(task_db.engine)==before
        original = contests._grant_once
        def mark(session, **kwargs):
            result=original(session,**kwargs)
            session.info['synthetic_lost_bonus_response']=True
            return result
        def lose(session):
            if session.info.get('synthetic_lost_bonus_response'):
                raise RuntimeError('synthetic committed response loss')
        monkeypatch.setattr(contests,'_grant_once',mark)
        event.listen(task_db.class_,'after_commit',lose)
        try:
            with pytest.raises(RuntimeError,match='synthetic committed response loss'):
                client.post(ROUTE,json=body)
        finally:
            event.remove(task_db.class_,'after_commit',lose)
        committed=database_rows(task_db.engine)
        assert len(committed['quest_completions'])==1
        assert len(committed['reward_grants'])==len(before['reward_grants'])+1
        retry=client.post(ROUTE,json=body)
        assert retry.status_code==200 and not retry.json()['created']
        assert retry.json()['dandelions_awarded']==0
        assert database_rows(task_db.engine)==committed


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_precommit_nonce_revocation_rolls_back_business_rows(task_db, monkeypatch):
    from app.session_revocations import NONCE, retire
    original=contests._authorized_bonus
    refreshes=[]
    def changed(request,session,*,refresh=False):
        if refresh:
            refreshes.append(True)
            if len(refreshes)==2:
                retire(request.session[NONCE])
        return original(request,session,refresh=refresh)
    with task_db.client(1) as client:
        body=body_for(client)
        cookie=client.cookies.get('session')
        before=database_rows(task_db.engine)
        monkeypatch.setattr(contests,'_authorized_bonus',changed)
        # PostgreSQL permits the independent nonce retirement while the reward
        # transaction holds its profile/state locks. SQLite's single writer does not.
        assert client.post(ROUTE,json=body).status_code==401
        assert_only_session_retirement(before,database_rows(task_db.engine),cookie)


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('routes',[(False,False),(False,True),(True,False)])
def test_postgres_overlapping_bonus_and_alias_settle_once(task_db, monkeypatch, routes, record_property):
    from app.main import app
    f=task_db;db=f.engine
    first_client=f.client(1);second_client=TestClient(app)
    second_client.cookies.update(first_client.cookies)
    first_body=body_for(first_client,routes[0]);second_body=body_for(second_client,routes[1])
    before=database_rows(db);held=Event();release=Event();pids={}
    original=contests._grant_once
    def hold(session,**kwargs):
        pids['first']=session.scalar(text('SELECT pg_backend_pid()'))
        assert session.connection().get_isolation_level()=='READ COMMITTED'
        held.set();assert release.wait(15)
        return original(session,**kwargs)
    monkeypatch.setattr(contests,'_grant_once',hold)
    def query(cn,cursor,statement,parameters,context,many):
        pid=cn.connection.driver_connection.info.backend_pid
        if held.is_set() and 'FOR UPDATE' in statement and pid!=pids.get('first'):
            pids['second']=pid
    event.listen(db,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(first_client.post,route_for(routes[0]),json=first_body)
            assert held.wait(10)
            second=pool.submit(second_client.post,route_for(routes[1]),json=second_body)
            try:
                observe_block(db,pids,record_property,'bonus-current-alias-contention')
            finally:
                release.set()
            a,b=first.result(timeout=15),second.result(timeout=15)
            assert a.status_code==b.status_code==200,(a.text,b.text)
            assert a.json()['created'] is True and b.json()['created'] is False
    finally:
        release.set();event.remove(db,'before_cursor_execute',query)
        first_client.close();second_client.close()
    after=database_rows(db)
    assert len(after['quest_completions'])==1
    assert len(after['reward_grants'])==len(before['reward_grants'])+1
    assert after['camp_point_awards']==before['camp_point_awards']
    assert after['practice_charts']==before['practice_charts']
    with f() as session:
        state=session.get(WoodchuckState,1)
        assert state.revision==f.initial_revision+1
        assert state.state_json['progress']['credits']==29
    for name in before:
        if name not in ('quest_completions','reward_grants','woodchuck_states'):
            assert after[name]==before[name],name


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('change',['version','deleted','instrument','nonce','week-rollover'])
def test_postgres_bonus_rechecks_after_profile_wait(task_db, change, record_property):
    from app.main import app
    f=task_db;db=f.engine;c=f.client(1);body=body_for(c)
    cookie=c.cookies.get('session');held=Event();release=Event();pids={}
    before=database_rows(db)
    def mutate():
        with f() as session:
            profile=session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
            assert session.connection().get_isolation_level()=='READ COMMITTED'
            if change=='version':profile.session_version+=1
            if change=='deleted':profile.status='deleted'
            if change=='instrument':profile.instrument='Tuba'
            pids['first']=session.scalar(text('SELECT pg_backend_pid()'))
            held.set();assert release.wait(15);session.commit()
    def query(cn,cursor,statement,parameters,context,many):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=cn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(db,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(mutate);assert held.wait(10)
            second=pool.submit(c.post,ROUTE,json=body)
            try:
                observe_block(db,pids,record_property,'bonus-postwait-'+change)
                if change=='nonce':
                    logout=TestClient(app);logout.cookies.update(c.cookies)
                    assert logout.post('/account/logout').status_code==200
                    logout.close()
                if change=='week-rollover':f.clock[0]=END
            finally:release.set()
            first.result(timeout=15);response=second.result(timeout=15)
    finally:release.set();event.remove(db,'before_cursor_execute',query);c.close()
    assert response.status_code=={'version':401,'deleted':401,'instrument':409,'nonce':401,'week-rollover':400}[change],response.text
    after=database_rows(db)
    for name in before:
        if name not in ('woodchuck_profiles','revoked_browser_sessions'):
            assert after[name]==before[name],name
    if change in ('version','deleted','nonce'):
        # Compare all other tables directly above; exact nonce retirement is
        # independently asserted while allowing the intentional profile mutation.
        adjusted={**before,'woodchuck_profiles':after['woodchuck_profiles']}
        assert_only_session_retirement(adjusted,after,cookie)


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('ordering',['withdraw-first','bonus-first'])
def test_postgres_parent_withdrawal_and_bonus_orderings(arcade_factory, monkeypatch, ordering, record_property):
    from app.main import app
    from app import tester_enrollments as testers, child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    from tests.test_arcade_economy import signed_client
    f=arcade_factory;db=f.engine;f.clock=[NOW-timedelta(days=1)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            instant=utc_instant(f.clock[0]);return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(contests,'datetime',Clock)
    monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    with f() as session:
        p=session.scalar(select(WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    child=TestClient(app)
    assert child.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
    # Preserve a valid historical completion and receipt across the race.
    assert child.post(ROUTE,json=body_for(child)).status_code==200
    f.clock[0]=NOW;body=body_for(child)
    other,_=signed_client(f,'OTHER')
    historical=database_rows(db);held=Event();release=Event();pids={}
    def withdraw():
        with f() as session:
            session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==pid).with_for_update())
            assert session.connection().get_isolation_level()=='READ COMMITTED'
            consent.withdraw_evidence(session,session.scalar(select(ConsentEvidence)))
            if ordering=='withdraw-first':
                pids['first']=session.scalar(text('SELECT pg_backend_pid()'))
                held.set();assert release.wait(15)
            session.commit()
    original=contests._grant_once
    def hold(session,**kwargs):
        pids['first']=session.scalar(text('SELECT pg_backend_pid()'))
        assert session.connection().get_isolation_level()=='READ COMMITTED'
        held.set();assert release.wait(15)
        return original(session,**kwargs)
    if ordering=='bonus-first':monkeypatch.setattr(contests,'_grant_once',hold)
    def query(cn,cursor,statement,parameters,context,many):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            backend=cn.connection.driver_connection.info.backend_pid
            if backend!=pids['first']:pids['second']=backend
    event.listen(db,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(withdraw if ordering=='withdraw-first' else lambda:child.post(ROUTE,json=body))
            assert held.wait(10)
            second=pool.submit((lambda:child.post(ROUTE,json=body)) if ordering=='withdraw-first' else withdraw)
            try:observe_block(db,pids,record_property,'parent-bonus-'+ordering)
            finally:release.set()
            a,b=first.result(timeout=15),second.result(timeout=15)
            response=b if ordering=='withdraw-first' else a
    finally:release.set();event.remove(db,'before_cursor_execute',query)
    assert response.status_code==(401 if ordering=='withdraw-first' else 200),response.text
    after=database_rows(db)
    assert all(row in after['quest_completions'] for row in historical['quest_completions'])
    assert all(row in after['reward_grants'] for row in historical['reward_grants'])
    extra=0 if ordering=='withdraw-first' else 1
    assert len(after['quest_completions'])==len(historical['quest_completions'])+extra
    assert len(after['reward_grants'])==len(historical['reward_grants'])+extra
    assert after['practice_charts']==historical['practice_charts']
    assert after['camp_point_awards']==historical['camp_point_awards']
    assert child.post(ROUTE,json=body).status_code==401
    # The normal mandatory nonce-retirement write is allowed; no new business reward.
    denied=database_rows(db)
    for table in after:
        if table!='revoked_browser_sessions':assert denied[table]==after[table],table
    with f() as session:
        assert session.scalar(select(ConsentEvidence)).withdrawn_at is not None
    if ordering=='bonus-first':monkeypatch.setattr(contests,'_grant_once',original)
    unaffected=other.post(ROUTE,json=body_for(other))
    assert unaffected.status_code==200,unaffected.text
    child.close();other.close()


@pytest.mark.parametrize('case',[
    'test_fallback_instances_are_canonical_and_instrument_specific',
    'test_full_completion_route_persists_reward_once_and_returns_authority',
    'test_server_grants_nothing_before_configured_threshold',
    'test_bonus_progress_endpoint_persists_increment_then_rewards_threshold',
    'test_piano_keyboard_get_and_post_share_canonical_challenge',
    'test_shared_resolver_replaces_stale_cross_instrument_client_state',
    'test_genuinely_missing_bonus_configuration_is_controlled',
    'test_multiple_tabs_are_idempotent',
    'test_failed_reward_insert_rolls_back_completion_and_state',
])
def test_existing_bonus_persistence_on_migrated_schema(arcade_factory,case,record_property):
    from tests import test_quest_completion_persistence as original
    record_property('original_test','tests/test_quest_completion_persistence.py::'+case)
    getattr(original,case)(arcade_factory)


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('case',['test_bonus_self_attestation_and_compatibility_alias_are_once_only',
    'test_bonus_invalid_assignments_dates_and_durations_never_complete'])
def test_existing_bonus_negative_security_on_migrated_schema(arcade_factory,legacy,case,record_property):
    from tests import test_sec003_authority as original
    getattr(original,case)(arcade_factory,legacy)
    record_property('original_test','tests/test_sec003_authority.py::'+case)


@pytest.mark.parametrize('activity',['hours','care','marching'])
def test_unrelated_board_fixed_reward_unchanged(arcade_factory,activity,record_property):
    from tests.test_sec003_authority import test_board_self_attestations_award_only_fixed_daily_value
    test_board_self_attestations_award_only_fixed_daily_value(arcade_factory,activity)
    record_property('original_test','tests/test_sec003_authority.py::test_board_self_attestations_award_only_fixed_daily_value')


def test_bonus_copy_and_existing_handler_contract():
    from pathlib import Path
    from tests.test_quest_completion_persistence import test_browser_handler_is_single_request_confirmed_ui_and_audio_safe
    test_browser_handler_is_single_request_confirmed_ui_and_audio_safe()
    root=Path(__file__).resolve().parents[1]
    source=(root/'static/js/app.js').read_text()
    assert 'Challenge complete: +5 Dandelions.' in source
    assert 'Challenge complete: +5 dandelions and +2 Board Activity Points.' not in source
    assert 'Bonus Challenge earns +5 Dandelions once daily and no Activity' in (root/'templates/quest.html').read_text()
    assert '/static/js/app.js?v=wl2-09' in (root/'templates/base.html').read_text()


def test_all_task_reads_preserve_populated_history(arcade_factory,monkeypatch):
    from tests.test_weekly2_foundation_migration import seed_history
    from app.age_privacy import declare_age
    from app.feature_access import FEATURES,Feature
    f=arcade_factory;f.clock=[NOW];seed_history(f.engine)
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(True,'open'))
    with f() as session:
        declare_age(session,1,'adult',at=NOW-timedelta(days=1));session.commit()
    plan=issue(f);assign(f,plan,'Trumpet');chart(f,instrument='Trumpet',minutes=15)
    at=utc_instant(NOW+timedelta(seconds=1))
    with f() as session:
        session.add(QuestCompletion(profile_id=1,activity_date=NOW.date(),quest_id='synthetic-read-provenance',
            logged_minutes=10,reward_amount=5,completed_at=at,created_at=at))
        session.add(RewardGrant(profile_id=1,source_key=f'bonus-challenge:{NOW.date()}:synthetic-read-provenance',
            reward_type='dandelion',amount=5,category_key='board-bonus-v1:trumpet',created_at=at))
        session.commit()
    before=database_rows(f.engine)
    assert before['classroom_reporting_periods'] and before['team_week_membership_snapshots']
    assert before['contest_results'] and before['reward_grants']
    with f() as session:
        p=session.get(WoodchuckProfile,1);saved=session.get(WeeklyPlan,plan.id)
        for _ in range(3):
            result=plans.current_plan_payload(session,p,saved,now=NOW+timedelta(minutes=2))
            assert result['tasks']['homework']['qualified'] is True
            assert result['tasks']['board']=={'target':2,'completed':1,'qualified':False}
            assert set(result['tasks'])=={'thirds','homework','board'}
            assert_student_safe(result)
            assert 'PRIVATE' not in json.dumps(result)
        session.commit()
    assert database_rows(f.engine)==before


def test_existing_trivia_authority_and_once_only_reward_unchanged(task_db):
    with task_db.client(1) as first, task_db.client(2) as other:
        today=task_db.clock[0].astimezone(CENTRAL).date()
        question=contests.trivia_question_for(today)
        body={'activity_date':today.isoformat(),'selected_answer_id':question['correct_answer_id']}
        result=first.post('/contests/trivia/answer',json=body)
        assert result.status_code==200 and result.json()['award_created'] is True
        sealed=database_rows(task_db.engine)
        retry=first.post('/contests/trivia/answer',json=body)
        assert retry.status_code==200 and retry.json()['award_created'] is False
        assert database_rows(task_db.engine)==sealed
        wrong=next(x['id'] for x in question['choices'] if x['id']!=question['correct_answer_id'])
        assert other.post('/contests/trivia/answer',json={**body,'selected_answer_id':wrong}).json()['correct'] is False
        assert other.post('/contests/trivia/answer',json=body).json()['correct'] is False
        with task_db() as session:
            assert session.get(WoodchuckState,1).state_json['progress']['credits']==25
            assert session.get(WoodchuckState,2).state_json['progress']['credits']==24
