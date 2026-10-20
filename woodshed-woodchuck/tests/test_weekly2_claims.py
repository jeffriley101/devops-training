"""Accepted B3b2 assertion functions on current migrated, guarded fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import sqlite3
from threading import Event
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from app import contests, weekly_caper as caper, weekly_plans as plans, weekly_routes as routes
from app.feature_access import FEATURES, Feature
from app.models import QuestCompletion, RewardGrant, WoodchuckProfile
from app.weekly import utc_instant
from app.weekly_models import WeeklyPlan, WeeklyTaskClaim
from app.weekly_rewards import grant, wallet
from tests.test_weekly2_tasks import END, NOW, assign, chart, issue, rows, task_db, plan_db, runtime, db, assert_student_safe
from tests.test_weekly2_thirds import recorded_round, arcade_factory
from tests.test_weekly2_plans import observe_block, rows as all_rows


@pytest.fixture
def claim_db(task_db, monkeypatch):
    monkeypatch.setattr(caper, "utc_instant", lambda value=None: utc_instant(value or task_db.clock[0]))
    return task_db


def snapshot(factory):
    result = rows(factory)
    with factory() as session:
        for name in ("weekly_task_claims", "contest_weeks", "contest_results"):
            result[name] = session.execute(text(f"SELECT * FROM {name} ORDER BY 1")).all()
    return result


def call(factory, operation, *args, pid=1):
    with factory() as session:
        result = operation(session, session.get(WoodchuckProfile, pid), *args, now=factory.clock[0])
        session.commit()
        return result


def board_receipt(factory, plan, day):
    # Synthetic committed producer artifact; the frozen B3b-1 route tests prove
    # real I Played It validation and atomic +5/zero-Activity production.
    completed = utc_instant(day)
    quest = "synthetic-accepted-bonus"
    with factory() as session:
        session.add(QuestCompletion(profile_id=plan.profile_id, quest_id=quest,
            activity_date=day.date(), logged_minutes=1, reward_amount=5,
            created_at=completed, completed_at=completed))
        session.add(RewardGrant(profile_id=plan.profile_id,
            source_key=f"bonus-challenge:{day.date()}:{quest}", reward_type="dandelion",
            amount=5, category_key="board-bonus-v1:flute", created_at=completed))
        contests._add_dandelion(session, plan.profile_id, 5)
        session.commit()


def earned_plan(factory, *, at=NOW):
    factory.clock[0] = at
    plan = issue(factory)
    assign(factory, plan, started=at)
    for offset in range(4):
        submitted = at + timedelta(days=offset, minutes=1)
        chart(factory, practiced=submitted.date(), submitted=submitted, minutes=15)
    for offset in (1, 2):
        board_receipt(factory, plan, at + timedelta(days=offset))
    recorded_round(factory, score=7, start=at + timedelta(seconds=1), end=at + timedelta(minutes=1))
    factory.clock[0] = at + timedelta(days=6)
    return plan


def claim_all(factory, plan):
    return [call(factory, caper.claim_task, plan.id, lane) for lane in caper.LANES]


def seed_clues(factory, count):
    # Historical durable ledger is authority for threshold-only tests; no fake
    # client completion or task writer is exposed by the product.
    with factory() as session:
        for offset in range(1, count + 1):
            day = NOW.date() - timedelta(weeks=offset)
            grant(session, 1, f"clover-caper:{day}:clue", "caper_clue", now=NOW)
        session.commit()


@pytest.mark.parametrize("lane", caper.LANES)
def test_each_lane_rejects_without_trusted_qualification(claim_db, lane):
    plan = issue(claim_db)
    before = snapshot(claim_db)
    with claim_db.client(1) as client:
        state = client.get("/weekly/plan").json()["tasks"][lane]
        assert not state["qualified"] and not state["claimable"] and not state["claimed"]
        response = client.post(f"/weekly/plans/{plan.id}/tasks/{lane}/claim", json={})
        assert response.status_code == 409
    assert snapshot(claim_db) == before


def test_five_lane_route_flow_three_jars_and_acknowledgements_only(claim_db):
    plan = earned_plan(claim_db)
    assign(claim_db, plan, "Tuba", started=NOW + timedelta(minutes=1))
    before = snapshot(claim_db)
    with claim_db.client(1) as client:
        for path in ("/weekly/plan", "/weekly/claims"):
            response = client.get(path)
            assert response.status_code == 200
            assert_student_safe(response.json())
            assert "PRIVATE" not in response.text and "recent_results" not in response.text
        assert snapshot(claim_db) == before
        tasks = client.get("/weekly/plan").json()["tasks"]
        assert set(tasks) == set(caper.LANES)
        assert all(t["qualified"] and t["claimable"] and not t["claimed"] for t in tasks.values())
        assert client.post(f"/weekly/plans/{plan.id}/clue/claim", json={}).status_code == 409
        for index, lane in enumerate(caper.LANES):
            path = f"/weekly/plans/{plan.id}/tasks/{lane}/claim"
            first = client.post(path, json={})
            assert first.status_code == 200, first.text
            value = first.json()
            assert value["qualified"] and value["claimed"] and not value["claimable"]
            assert value["reward"] == ({"type": "pickled_carrot", "amount": 1} if index < 3 else None)
            assert client.post(path, json={}).json() == value
            with claim_db() as session:
                assert wallet(session, 1)["pickled_carrot"] == min(index + 1, 3)
                assert wallet(session, 1)["caper_clue"] == 0
            if index < 4:
                assert client.post(f"/weekly/plans/{plan.id}/clue/claim", json={}).status_code == 409
        assert client.get("/weekly/claims").json()["current_plan"]["clue"]["claimable"]
        # Both assignments may qualify, but the second can never mint jar #4.
        chart(claim_db, practiced=(NOW + timedelta(days=1)).date(),
              submitted=NOW + timedelta(days=1, hours=1), minutes=15, instrument="Tuba")
        retry = client.post(f"/weekly/plans/{plan.id}/tasks/homework/claim", json={}).json()
        assert all(item["qualified"] for item in retry["assignments"])
        with claim_db() as session:
            assert wallet(session, 1)["pickled_carrot"] == 3
            assert set(session.scalars(select(WeeklyTaskClaim.lane))) == {"board", "thirds"}
            jars = session.scalars(select(RewardGrant.source_key).where(RewardGrant.reward_type == "pickled_carrot")).all()
            assert set(jars) == {f"clover-caper:{plan.week_start}:jar:{i}" for i in (1, 2, 3)}
        after = snapshot(claim_db)
        for table in before.keys() - {"reward_grants", "weekly_task_claims", "practice_charts"}:
            assert after[table] == before[table]  # Includes normal BOARD wallet/Activity/XP.
        assert client.post(f"/weekly/plans/{plan.id}/clue/claim", json={}).json()["clue"]["claimed"]
        first = client.get("/weekly/claims").json()
        assert client.post(f"/weekly/plans/{plan.id}/clue/claim", json={}).json()["clue_count"] == 1
        saved = snapshot(claim_db)
        assert client.get("/weekly/claims").json() == first
        payload = client.get("/weekly/plan").json()
        assert all(t["claimed"] and not t["claimable"] for t in payload["tasks"].values())
        assert snapshot(claim_db) == saved
        print("CLAIMS_EXAMPLE", json.dumps(first, sort_keys=True))
        print("PLAN_TASKS_EXAMPLE", json.dumps(payload["tasks"], sort_keys=True))


@pytest.mark.parametrize("offset,qualifies", [(-1, True), (0, False), (1, False)],
                         ids=["before-next-monday", "exact-next-monday", "after-next-monday"])
def test_source_week_cutoff_allows_late_claim_but_not_late_activity(claim_db, offset, qualifies):
    plan = issue(claim_db)
    assign(claim_db, plan)
    for day in range(3):
        moment = NOW + timedelta(days=day, minutes=1)
        chart(claim_db, practiced=moment.date(), submitted=moment, minutes=15, instrument="Tuba")
    chart(claim_db, practiced=(END - timedelta(days=1)).date(), submitted=END + timedelta(microseconds=offset), minutes=15)
    claim_db.clock[0] = END + timedelta(days=3)
    with claim_db() as session:
        state = caper.task_states(session, 1, plan, now=claim_db.clock[0])
        assert plans.current_general_progress(session, 1, now=claim_db.clock[0], week_start=plan.week_start)["practice_seconds"] == 3600
        for lane in caper.JAR_LANES:
            assert state[lane]["qualified"] is qualifies
    # A settled competition is independent of earned task claims.
    with claim_db() as session:
        _, _, week = contests.ensure_current_contest_data(session, now=NOW)
        week.status = "finalized"
        session.commit()
    before = snapshot(claim_db)
    for lane in caper.JAR_LANES:
        if qualifies:
            assert call(claim_db, caper.claim_task, plan.id, lane)["claimed"]
        else:
            with pytest.raises(HTTPException) as error:
                call(claim_db, caper.claim_task, plan.id, lane)
            assert error.value.status_code == 409
    after = snapshot(claim_db)
    assert after["contest_weeks"] == before["contest_weeks"]
    assert after["contest_results"] == before["contest_results"]
    with claim_db() as session:
        assert wallet(session, 1)["pickled_carrot"] == (3 if qualifies else 0)


def test_fourteen_pending_clues_require_two_explicit_clicks(claim_db):
    seed_clues(claim_db, 14)
    first = call(claim_db, caper.claim_clover, "first")
    assert first["white_clover_balance"] == 1 and first["clover_claimable"]
    assert call(claim_db, caper.claim_clover, "first") == first
    with claim_db() as session:
        assert list(session.scalars(select(RewardGrant.source_key).where(RewardGrant.reward_type == "white_clover"))) == ["clover-caper:clues:7"]
    second = call(claim_db, caper.claim_clover, "second")
    assert second["white_clover_balance"] == 2 and not second["clover_claimable"]
    assert second["clue_count"] == 14
    assert call(claim_db, caper.claim_clover, "second") == second


@pytest.mark.parametrize("kind", ["jar", "acknowledgement", "clue", "clover"])
@pytest.mark.parametrize("failure", ["after-sql", "before-commit"])
def test_claim_sql_and_commit_failure_rolls_back_entire_outcome(claim_db, kind, failure):
    plan = earned_plan(claim_db)
    if kind == "clue": claim_all(claim_db, plan)
    if kind == "clover": seed_clues(claim_db, 7)
    operation, args = {
        "jar": (caper.claim_task, (plan.id, "practice_minutes")),
        "acknowledgement": (caper.claim_task, (plan.id, "board")),
        "clue": (caper.claim_clue, (plan.id,)),
        "clover": (caper.claim_clover, ("rollback-click",)),
    }[kind]
    before = snapshot(claim_db)
    target = claim_db.class_ if failure == "before-commit" else WeeklyTaskClaim if kind == "acknowledgement" else RewardGrant
    event_name = "before_commit" if failure == "before-commit" else "after_insert"
    def fail(*args): raise RuntimeError("synthetic failure after claim SQL")
    event.listen(target, event_name, fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            call(claim_db, operation, *args)
    finally:
        event.remove(target, event_name, fail)
    assert snapshot(claim_db) == before
    assert call(claim_db, operation, *args)["claimed" if kind != "clue" else "clue"]


def test_strict_fields_foreign_plan_gate_and_no_hidden_lanes(claim_db, monkeypatch):
    plan = earned_plan(claim_db)
    before = snapshot(claim_db)
    with claim_db.client(2) as client:
        for path in (f"/weekly/plans/{plan.id}/tasks/logging/claim", f"/weekly/plans/{plan.id}/clue/claim"):
            assert client.post(path, json={}).status_code == 404
        assert client.get("/weekly/claims").json()["current_plan"] is None
    with claim_db.client(1) as client:
        for lane in ("6", "pristine", "plunge", "history", "Logging"):
            assert client.post(f"/weekly/plans/{plan.id}/tasks/{lane}/claim", json={}).status_code == 422
        for field in ("profile_id", "score", "minutes", "progress", "completed", "amount", "target", "week", "clue_count", "threshold"):
            for path, body in ((f"/weekly/plans/{plan.id}/tasks/logging/claim", {}),
                               (f"/weekly/plans/{plan.id}/clue/claim", {}),
                               ("/weekly/clover/claim", {"request_key": "strict"})):
                assert client.post(path, json={**body, field: 999}).status_code == 422
                assert client.post(path, json=body, params={field: 999}).status_code == 422
            assert client.get("/weekly/claims", params={field: 999}).status_code == 422
        monkeypatch.setitem(FEATURES, "weekly_plans", Feature(False, "open"))
        assert client.get("/weekly/claims").status_code == 403
        assert client.post(f"/weekly/plans/{plan.id}/tasks/logging/claim", json={}).status_code == 403
    assert snapshot(claim_db) == before


def test_bounded_history_keeps_old_entitlements_reachable_and_reads_do_not_write(claim_db):
    for offset in range(15, 0, -1):
        claim_db.clock[0] = NOW - timedelta(weeks=offset)
        issue(claim_db)
    current = earned_plan(claim_db)
    before = snapshot(claim_db)
    with claim_db.client(1) as client:
        first = client.get("/weekly/claims").json()
        assert first["current_plan"]["plan_id"] == current.id
        assert len(first["historical_plans"]) == 12
        second = client.get("/weekly/claims", params={"before_plan_id": first["next_before_plan_id"]}).json()
        assert len(second["historical_plans"]) == 3 and second["next_before_plan_id"] is None
        assert len({p["plan_id"] for p in first["historical_plans"] + second["historical_plans"]}) == 15
        assert_student_safe(first)
        assert "recent_results" not in json.dumps(first)
    assert snapshot(claim_db) == before


def test_only_identified_claim_uniqueness_is_retryable():
    for message in ("UNIQUE constraint failed: weekly_task_claims.plan_id, weekly_task_claims.lane",
                    "UNIQUE constraint failed: reward_grants.profile_id, reward_grants.source_key, reward_grants.reward_type"):
        assert caper.is_claim_unique_race(IntegrityError("", {}, sqlite3.IntegrityError(message)), "sqlite")
    for message in ("CHECK constraint failed: ck_weekly_task_claim_lane", "FOREIGN KEY constraint failed",
                    "NOT NULL constraint failed: weekly_task_claims.claimed_at"):
        assert not caper.is_claim_unique_race(IntegrityError("", {}, sqlite3.IntegrityError(message)), "sqlite")


def test_route_retries_whole_claim_only_for_known_uniqueness(claim_db, monkeypatch):
    plan = earned_plan(claim_db)
    original = caper.claim_task
    attempts = []
    def raced(session, profile, *args, **kwargs):
        attempts.append(profile.id)
        result = original(session, profile, *args, **kwargs)
        if len(attempts) == 1:
            raise IntegrityError("claim", {}, sqlite3.IntegrityError(
                "UNIQUE constraint failed: reward_grants.profile_id, reward_grants.source_key, reward_grants.reward_type"))
        return result
    monkeypatch.setattr(caper, "claim_task", raced)
    with claim_db.client(1) as client:
        assert client.post(f"/weekly/plans/{plan.id}/tasks/logging/claim", json={}).status_code == 200
    assert attempts == [1, 1]
    with claim_db() as session:
        assert wallet(session, 1)["pickled_carrot"] == 1


def test_claim_refreshes_authority_after_profile_fence(claim_db, monkeypatch):
    plan = earned_plan(claim_db)
    original = caper._lock_profile
    def revoke_before_fence(session, profile):
        with claim_db() as other:
            other.execute(text("UPDATE woodchuck_profiles SET session_version=session_version+1 WHERE id=:id"), {"id": profile.id})
            other.commit()
        return original(session, profile)
    monkeypatch.setattr(caper, "_lock_profile", revoke_before_fence)
    with claim_db.client(1) as client:
        assert client.post(f"/weekly/plans/{plan.id}/tasks/logging/claim", json={}).status_code == 401
    with claim_db() as session:
        assert wallet(session, 1)["pickled_carrot"] == 0


def test_disabled_claim_gate_avoids_claim_table(claim_db, monkeypatch):
    plan = issue(claim_db)
    monkeypatch.setitem(FEATURES, "weekly_plans", Feature(False, "open"))
    with claim_db.kw["bind"].begin() as connection:
        connection.execute(text("DROP TABLE weekly_task_claims"))
    with claim_db.client(1) as client:
        assert client.get("/weekly/claims").status_code == 403
        assert client.get("/weekly/plan").status_code == 403
        assert client.post(f"/weekly/plans/{plan.id}/tasks/logging/claim", json={}).status_code == 403
        assert client.post(f"/weekly/plans/{plan.id}/clue/claim", json={}).status_code == 403
        assert client.post("/weekly/clover/claim", json={"request_key": "off"}).status_code == 403



def test_clue_seven_separate_clover_and_unrelated_sources(claim_db):
    seed_clues(claim_db,6)
    with pytest.raises(HTTPException) as error:call(claim_db,caper.claim_clover,'six')
    assert error.value.status_code==409
    plan=earned_plan(claim_db);claim_all(claim_db,plan)
    clue=call(claim_db,caper.claim_clue,plan.id)
    assert clue['clue_count']==7 and clue['clover_claimable'] and clue['white_clover_balance']==0
    first=call(claim_db,caper.claim_clover,'first')
    assert first['white_clover_balance']==1 and not first['clover_claimable']
    seed_clues(claim_db,13)
    with claim_db() as s:
        before=caper.clover_state(s,1)
        assert before['clue_count']==14 and before['next_clover_threshold']==14
        grant(s,1,'synthetic-other-source:1','white_clover',now=claim_db.clock[0]);s.commit()
        after=caper.clover_state(s,1)
        assert after=={**before,'white_clover_balance':before['white_clover_balance']+1}
    retry=call(claim_db,caper.claim_clover,'first')
    assert retry['claimed_at']==first['claimed_at'] and retry['clover_claimable']
    final=call(claim_db,caper.claim_clover,'second')
    assert final['white_clover_balance']==3 and final['clue_count']==14 and not final['clover_claimable']
    assert call(claim_db,caper.claim_clover,'second')==final


def test_claim_durable_after_chart_deletion_spending_and_lost_response(claim_db,monkeypatch):
    plan=earned_plan(claim_db)
    with claim_db.client(1) as client:
        path=f'/weekly/plans/{plan.id}/tasks/practice_minutes/claim'
        original=caper.claim_task
        def mark(s,*args,**kwargs):
            value=original(s,*args,**kwargs);s.info['lost_claim']=True;return value
        def lose(s):
            if s.info.get('lost_claim'):raise RuntimeError('synthetic lost committed response')
        monkeypatch.setattr(caper,'claim_task',mark)
        event.listen(claim_db.class_,'after_commit',lose)
        try:
            with pytest.raises(RuntimeError,match='synthetic lost committed response'):client.post(path,json={})
        finally:event.remove(claim_db.class_,'after_commit',lose)
        committed=snapshot(claim_db)
        retry=client.post(path,json={});assert retry.status_code==200 and retry.json()['claimed']
        assert snapshot(claim_db)==committed
        timestamp=retry.json()['claimed_at']
        with claim_db() as s:
            s.execute(text('DELETE FROM practice_charts WHERE profile_id=1'))
            grant(s,1,'synthetic-spending:1','pickled_carrot',-1,now=claim_db.clock[0]);s.commit()
        before=snapshot(claim_db)
        data=client.get('/weekly/plan').json()['tasks']['practice_minutes']
        assert data['progress']==0 and data['qualified'] and data['claimed'] and not data['claimable']
        assert data['claimed_at']==timestamp
        assert client.post(path,json={}).json()['claimed_at']==timestamp
        with claim_db() as s:assert wallet(s,1)['pickled_carrot']==0
        assert snapshot(claim_db)==before


@pytest.mark.parametrize('origin',['https://foreign.invalid','http://testserver:8123','null'])
def test_claim_origin_and_invalid_clover_keys_leave_rows_unchanged(claim_db,origin):
    plan=earned_plan(claim_db)
    with claim_db.client(1) as client:
        before=snapshot(claim_db)
        for path,body in [(f'/weekly/plans/{plan.id}/tasks/logging/claim',{}),
                          (f'/weekly/plans/{plan.id}/clue/claim',{}),('/weekly/clover/claim',{'request_key':'foreign'})]:
            assert client.post(path,json=body,headers={'Origin':origin}).status_code==403
        for key in ['', 'x'*65, 2, None, [], {}]:
            assert client.post('/weekly/clover/claim',json={'request_key':key}).status_code==422
        assert snapshot(claim_db)==before


@pytest.mark.parametrize('change',['version','deleted','account','nonce'])
def test_claim_account_and_session_denials(claim_db,change):
    from app.main import app
    with claim_db.client(1) as c:
        plan=earned_plan(claim_db);cookie=c.cookies.get('session')
        if change in ('version','deleted'):
            with claim_db() as s:
                p=s.get(WoodchuckProfile,1)
                if change=='version':p.session_version+=1
                else:p.status='deleted'
                s.commit()
        if change=='nonce':
            other=TestClient(app);other.cookies.update(c.cookies)
            assert other.post('/account/logout').status_code==200;other.close()
        headers={'x-woodshed-account':'WL2-PLAN-2'} if change=='account' else {}
        before=all_rows(claim_db.engine)
        for path,body in [(f'/weekly/plans/{plan.id}/tasks/logging/claim',{}),
                          (f'/weekly/plans/{plan.id}/clue/claim',{}),('/weekly/clover/claim',{'request_key':'denied'})]:
            assert c.post(path,json=body,headers=headers).status_code==401
        assert c.get('/weekly/claims',headers=headers).status_code==401
        from tests.test_weekly2_game_mode import assert_only_session_retirement
        if change == "account":
            assert all_rows(claim_db.engine) == before
        else:
            assert_only_session_retirement(before,all_rows(claim_db.engine),cookie)


@pytest.mark.parametrize('kind',['jar','acknowledgement','clue','same-key','different-key'])
@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_postgres_claim_overlap_exactly_once(claim_db,kind,monkeypatch,record_property):
    from app.main import app
    f=claim_db;engine=f.engine;plan=earned_plan(f)
    if kind=='clue':claim_all(f,plan)
    if kind in ('same-key','different-key'):seed_clues(f,7)
    a=f.client(1);b=TestClient(app);b.cookies.update(a.cookies)
    if kind=='jar':path=f'/weekly/plans/{plan.id}/tasks/logging/claim';body={};body2={}
    elif kind=='acknowledgement':path=f'/weekly/plans/{plan.id}/tasks/board/claim';body={};body2={}
    elif kind=='clue':path=f'/weekly/plans/{plan.id}/clue/claim';body={};body2={}
    else:path='/weekly/clover/claim';body={'request_key':'action-a'};body2={'request_key':'action-b' if kind=='different-key' else 'action-a'}
    before=all_rows(engine);held=Event();release=Event();pids={}
    def hold(s):
        if not s.info.get('claim_holder') and not held.is_set():
            s.info['claim_holder']=True
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'))
            assert s.connection().get_isolation_level()=='READ COMMITTED'
            held.set();assert release.wait(15)
    def query(cn,cursor,statement,parameters,context,many):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=cn.connection.driver_connection.info.backend_pid
            if pid!=pids.get('first'):pids['second']=pid
    event.listen(f.class_,'before_commit',hold);event.listen(engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(a.post,path,json=body);assert held.wait(10)
            second=pool.submit(b.post,path,json=body2)
            try:observe_block(engine,pids,record_property,'caper-'+kind)
            finally:release.set()
            x,y=first.result(timeout=15),second.result(timeout=15)
            assert x.status_code==200,x.text
            assert y.status_code==(409 if kind=='different-key' else 200),y.text
            if kind!='different-key':assert x.json()==y.json()
    finally:
        release.set();event.remove(f.class_,'before_commit',hold);event.remove(engine,'before_cursor_execute',query);a.close();b.close()
    after=all_rows(engine)
    allowed='weekly_task_claims' if kind=='acknowledgement' else 'reward_grants'
    for name in before:
        if name!=allowed:assert after[name]==before[name],name
    assert len(after[allowed])==len(before[allowed])+1


@pytest.mark.parametrize('change',['version','deleted','nonce'])
@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_postgres_claim_reauthorizes_after_wait(claim_db,change,record_property):
    from app.main import app
    f=claim_db;engine=f.engine;plan=earned_plan(f);c=f.client(1);cookie=c.cookies.get('session')
    before=all_rows(engine);held=Event();release=Event();pids={}
    def holder():
        with f() as s:
            p=s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
            if change=='version':p.session_version+=1
            if change=='deleted':p.status='deleted'
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));assert s.connection().get_isolation_level()=='READ COMMITTED'
            held.set();assert release.wait(15);s.commit()
    def query(cn,cursor,statement,parameters,context,many):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=cn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(holder);assert held.wait(10)
            second=pool.submit(c.post,f'/weekly/plans/{plan.id}/tasks/logging/claim',json={})
            try:
                observe_block(engine,pids,record_property,'caper-revocation-'+change)
                if change=='nonce':
                    other=TestClient(app);other.cookies.update(c.cookies);assert other.post('/account/logout').status_code==200;other.close()
            finally:release.set()
            first.result(timeout=15);response=second.result(timeout=15);assert response.status_code==401,response.text
            # The existing profile fence rejects a concurrent version change
            # before current_profile can clear session facts. A later ordinary
            # request performs normal retirement; neither request may claim.
            assert c.get('/weekly/claims').status_code==401
    finally:release.set();event.remove(engine,'before_cursor_execute',query);c.close()
    after=all_rows(engine)
    for name in before:
        if name not in ('woodchuck_profiles','revoked_browser_sessions'):assert after[name]==before[name],name
    from tests.test_weekly2_game_mode import assert_only_session_retirement
    assert_only_session_retirement({**before,'woodchuck_profiles':after['woodchuck_profiles']},after,cookie)


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_postgres_c002_consent_withdrawal_after_claim_wait(arcade_factory,monkeypatch,record_property):
    from app.main import app
    from app import tester_enrollments as testers,child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry,CLAIMED
    f=arcade_factory;engine=f.engine
    monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    with f() as s:p=s.scalar(select(WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    c=TestClient(app);assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(True,'open'))
    f.clock=[NOW]
    for module in (plans,routes,caper):monkeypatch.setattr(module,'utc_instant',lambda value=None:utc_instant(value or f.clock[0]))
    assert c.patch('/account/profile/game-mode',json={'game_mode':'Challenge'}).status_code==200
    plan=c.post('/weekly/plan',json={'focus':'Build daily consistency'}).json()['plan']
    for day in range(4):
        submitted=NOW+timedelta(days=day,minutes=1)
        chart(f,pid=pid,practiced=submitted.date(),submitted=submitted,minutes=15)
    f.clock[0]=NOW+timedelta(days=5)
    path=f"/weekly/plans/{plan['id']}/tasks/practice_minutes/claim"
    control=c.post(path,json={});assert control.status_code==200 and control.json()['claimed']
    assert_student_safe(c.get('/weekly/claims').json())
    before=all_rows(engine);held=Event();release=Event();pids={}
    def withdraw():
        with f() as s:
            s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==pid).with_for_update())
            assert s.connection().get_isolation_level()=='READ COMMITTED'
            consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cursor,statement,parameters,context,many):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            backend=cn.connection.driver_connection.info.backend_pid
            if backend!=pids['first']:pids['second']=backend
    event.listen(engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(withdraw);assert held.wait(10)
            second=pool.submit(c.post,f"/weekly/plans/{plan['id']}/tasks/logging/claim",json={})
            try:observe_block(engine,pids,record_property,'caper-C002-withdrawal')
            finally:release.set()
            first.result(timeout=15);response=second.result(timeout=15);assert response.status_code==401,response.text
    finally:release.set();event.remove(engine,'before_cursor_execute',query)
    after=all_rows(engine)
    for table in ('weekly_task_claims','reward_grants','weekly_plans','weekly_instrument_plans',
                  'practice_charts','woodchuck_states','contest_results'):
        assert after[table]==before[table],table
    assert c.get('/weekly/claims').status_code==401
    with f() as s:assert s.scalar(select(ConsentEvidence)).withdrawn_at is not None
    c.close()
