"""Additional independent-jar, carry-in and native security boundary contracts."""
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app import weekly_caper as caper
from app.models import RewardGrant
from app.weekly_rewards import wallet
from tests.test_weekly2_claims import claim_db, task_db, plan_db, runtime, db, earned_plan, snapshot
from tests.test_weekly2_tasks import NOW, issue, chart, assign


@pytest.mark.parametrize('lane',['logging','practice_minutes','homework'])
def test_each_jar_is_independent_and_retry_preserves_saved_receipt(claim_db,lane):
    plan=earned_plan(claim_db)
    with claim_db.client(1) as client:
        before=snapshot(claim_db)
        path=f'/weekly/plans/{plan.id}/tasks/{lane}/claim'
        first=client.post(path,json={})
        assert first.status_code==200 and first.json()['claimed'] and first.json()['reward']=={'type':'pickled_carrot','amount':1}
        state=client.get('/weekly/plan').json()['tasks']
        assert [key for key,value in state.items() if value['claimed']]==[lane]
        assert not client.get('/weekly/claims').json()['current_plan']['clue']['claimable']
        saved=snapshot(claim_db)
        assert client.post(path,json={}).json()==first.json() and snapshot(claim_db)==saved
        for name in before:
            if name!='reward_grants':assert saved[name]==before[name],name
        with claim_db() as s:
            assert wallet(s,1)['pickled_carrot']==1
            jar=s.scalar(select(RewardGrant).where(RewardGrant.reward_type=='pickled_carrot'))
            assert jar.source_key==f'clover-caper:{plan.week_start}:jar:{caper.JAR_LANES[lane]}'


def test_monday_preplan_carry_in_does_not_inherit_homework_start_cutoff(claim_db):
    plan=issue(claim_db)
    chart(claim_db,practiced=NOW.date(),submitted=NOW-timedelta(hours=1),minutes=60)
    assign(claim_db,plan,started=NOW)
    with claim_db.client(1) as client:
        before=snapshot(claim_db)
        tasks=client.get('/weekly/plan').json()['tasks']
        assert tasks['practice_minutes']['progress']==3600 and tasks['practice_minutes']['qualified']
        assert tasks['logging']['progress']==1
        assert tasks['homework']['assignments'][0]['practice_seconds']==0 and not tasks['homework']['qualified']
        assert snapshot(claim_db)==before
        assert client.post(f'/weekly/plans/{plan.id}/tasks/practice_minutes/claim',json={}).status_code==200
        assert client.post(f'/weekly/plans/{plan.id}/tasks/homework/claim',json={}).status_code==409


def test_native_missing_origin_and_unauthenticated_claims_fail_closed(claim_db):
    from app.main import app
    plan=earned_plan(claim_db)
    with claim_db.client(1) as client, TestClient(app) as anonymous:
        before=snapshot(claim_db)
        for path,body in [(f'/weekly/plans/{plan.id}/tasks/logging/claim',{}),
                          (f'/weekly/plans/{plan.id}/clue/claim',{}),('/weekly/clover/claim',{'request_key':'missing-origin'})]:
            request=client.build_request('POST',path,json=body)
            del request.headers['origin']
            assert client.send(request).status_code==403
            assert anonymous.post(path,json=body).status_code==401
        assert anonymous.get('/weekly/claims').status_code==401
        assert snapshot(claim_db)==before
