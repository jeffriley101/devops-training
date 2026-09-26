"""Balance attacks and earning regressions, only isolated SQLite/local PostgreSQL."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, arcade_routes, contests, main, practice_chart_routes, store_routes
from app.db import Base
from app.models import (CrownAward, OwnedItemCopy,
                        RewardGrant, WoodchuckProfile, WoodchuckState)
from app.security import hash_pin
from app.store_inventory import purchase_catalog_item
from tests.test_team_families import disposable_url


@pytest.fixture(params=['sqlite', 'postgresql'])
def economy_db(request, tmp_path, monkeypatch):
    engine = (create_engine('sqlite://', poolclass=StaticPool,
                            connect_args={'check_same_thread': False})
              if request.param == 'sqlite' else create_engine(disposable_url(tmp_path, 'postgresql')))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    from app import session_revocations
    monkeypatch.setattr(session_revocations, "SessionLocal", factory)
    for module in (account_routes, arcade_routes, contests, main, practice_chart_routes, store_routes):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.delenv('SMTP_HOST', raising=False)
    with factory() as session:
        for index in (1, 2):
            session.add(WoodchuckProfile(woodchuck_id=f'WC-ECONOMY{index}',
                display_name=f'Student {index}', pin_hash=hash_pin('2468'),
                instrument='Flute', level='Beginner', goal='Practice'))
        session.flush()
        from app.age_privacy import declare_age
        for profile_id in (1, 2):
            declare_age(session, profile_id, "adult", at=datetime(2000, 1, 1, tzinfo=timezone.utc))
        session.add_all([WoodchuckState(profile_id=i, revision=3, state_json={
            'account': {'serverRevision': 3}, 'progress': {'credits': 100, 'streak': 2},
            'inventory': {'ownedItems': ['legacy-hat']}, 'profile': {},
        }) for i in (1, 2)])
        session.commit()
    yield factory
    engine.dispose()


def signed_client(factory, index=1):
    client = TestClient(main.app)
    assert client.post('/account/login', data={
        'woodchuck_id': f'WC-ECONOMY{index}', 'pin': '2468',
    }).status_code == 200
    return client


def state(client):
    result = client.get('/account/state').json()
    result['state']['account']['serverRevision'] = result['revision']
    return result['state']


def balance(client):
    return state(client)['progress']['credits']


@pytest.mark.parametrize('progress', [{'credits': 999999}, {'credits': 102},
    {'credits': 0}, {'credits': -1}, {'credits': None}, {'credits': True},
    {'credits': '999999'}, {}, None, []])
def test_sync_cannot_increase_decrease_replace_or_drop_balance(economy_db, progress):
    client = signed_client(economy_db)
    before = state(client)
    forged = deepcopy(before)
    forged['progress'] = progress
    forged['preference'] = {'sound': False}
    response = client.put('/account/state', json=forged)
    assert response.status_code == 200
    after = state(client)
    assert after['progress']['credits'] == before['progress']['credits']
    assert after['preference'] == {'sound': False}
    assert after['account']['serverRevision'] == before['account']['serverRevision'] + 1
    assert response.json()['credits'] == before['progress']['credits']


def test_signup_and_missing_state_cannot_import_funds(economy_db):
    with TestClient(main.app) as client:
        result = client.post('/account/create', data=dict(age_band='adult', display_name='New student', pin='2468',
            instrument='Flute', level='Beginner', goal='Practice', initial_state=json.dumps({
                'progress': {'credits': 999999, 'streak': 999},
                'inventory': {'ownedItems': ['ufo']}, 'account': {'admin': True}})))
        assert result.status_code == 200
        assert balance(client) == 1  # The real first-day login reward only.
        assert state(client)['inventory']['ownedItems'] == []
        assert 'admin' not in state(client)['account']
        assert client.post('/store/purchases', json={'item_key': 'ufo'}).status_code == 409
    client = signed_client(economy_db)
    with economy_db() as session:
        session.delete(session.get(WoodchuckState, 1))
        session.commit()
    assert client.put('/account/state', json={'account': {'serverRevision': 0},
        'progress': {'credits': 999999}}).status_code == 200
    assert balance(client) == 0


def test_forged_funds_cannot_buy_or_create_owned_rewards(economy_db):
    client = signed_client(economy_db)
    forged = state(client)
    forged['progress']['credits'] = 999999
    forged['inventory'] = {'ownedItems': ['ufo'], 'crowns': ['all'], 'medals': ['gold']}
    forged['rewards'] = [{'amount': 999999}]
    assert client.put('/account/state', json=forged).status_code == 200
    assert client.post('/store/purchases', json={'item_key': 'ufo'}).status_code == 409
    assert client.get('/store/inventory').json()['items'] == []
    assert state(client)['inventory']['ownedItems'] == ['legacy-hat']
    with economy_db() as session:
        assert session.scalar(select(func.count()).select_from(OwnedItemCopy)) == 0
        assert session.scalar(select(func.count()).select_from(CrownAward)) == 0
        assert session.scalar(select(func.count()).select_from(RewardGrant)) == 1


def test_identity_and_other_account_stay_server_owned(economy_db):
    first, second = signed_client(economy_db), signed_client(economy_db, 2)
    other = state(second)
    forged = state(first)
    forged['account'].update(woodchuckId='WC-ECONOMY2', authenticated=False, admin=True)
    forged['profile'] = {'woodchuckName': 'Spoofed', 'instrument': 'Tuba', 'level': 'Legend',
                         'goal': 'Spoofed', 'createdAt': '1900-01-01'}
    forged['profile_id'] = 2
    forged['progress'].update(credits=999999, streak=999)
    assert first.put('/account/state?profile_id=2', json=forged).status_code == 200
    after = state(first)
    assert after['account']['woodchuckId'] == 'WC-ECONOMY1'
    assert after['account']['authenticated'] is True
    assert 'admin' not in after['account']
    assert after['profile']['woodchuckName'] == 'Student 1'
    assert after['profile']['instrument'] == 'Flute'
    assert after['profile']['level'] == 'Beginner'
    assert after['profile']['createdAt'] != '1900-01-01'
    assert after['progress']['streak'] == 2
    assert state(second) == other
    with TestClient(main.app) as anonymous:
        assert anonymous.put('/account/state', json=forged).status_code == 401


def test_practice_logs_do_not_grant_before_review(economy_db):
    client = signed_client(economy_db)
    before = balance(client)
    today = datetime.now(contests.CENTRAL).date().isoformat()
    chart = dict(practice_date=today, minutes=20, practice_details=['Scales', 'Long tones'],
                 credits_awarded=75, submission_key='practice-once')
    first = client.post('/practice-charts', json=chart)
    assert first.status_code == 201
    assert first.json()['chart']['credits_awarded'] == 0
    assert client.post('/practice-charts', json=chart).json()['created'] is False
    capped = client.post('/practice-charts', json={**chart, 'minutes':1440, 'submission_key':'cap'})
    assert capped.status_code == 400
    assert balance(client) == before


def test_board_earnings_once_and_trivia_requires_correct_answer(economy_db):
    client = signed_client(economy_db)
    before = balance(client)
    today = datetime.now(contests.CENTRAL).date()
    for activity in ('hours', 'care', 'marching'):
        body = {'activity_date': today.isoformat(), 'activity_type': activity}
        assert client.post('/contests/camp-points/awards', json=body).status_code == 400
        assert client.post('/contests/camp-points/awards', json=body).status_code == 400
    assert balance(client) == before
    assert client.post('/contests/camp-points/awards', json={
        'activity_date':today.isoformat(), 'activity_type':'trivia'}).status_code == 400
    question = contests.trivia_question_for(today)
    answer = {'activity_date': today.isoformat(), 'selected_answer_id': question['correct_answer_id']}
    assert client.post('/contests/trivia/answer', json=answer).json()['award_created'] is True
    assert client.post('/contests/trivia/answer', json=answer).json()['award_created'] is False
    assert balance(client) == before + 1
    other = signed_client(economy_db, 2)
    other_before = balance(other)
    wrong = next(c['id'] for c in question['choices'] if c['id'] != question['correct_answer_id'])
    assert other.post('/contests/trivia/answer', json={**answer, 'selected_answer_id':wrong}).json()['correct'] is False
    assert other.post('/contests/trivia/answer', json=answer).json()['correct'] is False
    assert balance(other) == other_before


@pytest.mark.parametrize('operation', ['secret', 'purchase', 'arcade'])
def test_stale_sync_cannot_erase_or_refund_intervening_economy(economy_db, operation):
    client = signed_client(economy_db)
    old = state(client)
    today = datetime.now(contests.CENTRAL).date().isoformat()
    if operation == 'secret':
        result = client.post('/account/daily-secret', json={'passcode':'union'})
    elif operation == 'practice':
        result = client.post('/practice-charts', json={'practice_date':today, 'minutes':30})
    elif operation == 'board':
        result = client.post('/contests/camp-points/awards', json={'activity_date':today, 'activity_type':'care'})
    elif operation == 'quest':
        challenge = client.get('/contests/bonus-challenge/current').json()['challenge']
        result = client.post('/contests/bonus-challenge/progress', json={
            'activity_date':today, 'challenge_instance':challenge['instance_key']})
    elif operation == 'purchase':
        result = client.post('/store/purchases', json={'item_key':'ladybug'})
    else:
        result = client.post('/arcade/plays', json={'request_id': uuid4().hex, 'game_key':'thirds'})
    assert result.status_code in (200, 201), result.text
    earned = balance(client)
    assert earned != old['progress']['credits']
    assert client.put('/account/state', json=old).status_code == 409
    assert balance(client) == earned
    fresh = state(client)
    fresh['progress']['credits'] = old['progress']['credits']
    assert client.put('/account/state', json=fresh).status_code == 200
    assert balance(client) == earned


def require_postgres(factory):
    if factory.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Row-lock concurrency proof requires disposable PostgreSQL')


@pytest.mark.parametrize('operation', ['reward', 'purchase'])
def test_postgres_sync_waits_for_economy_commit_then_rejects_stale(economy_db, operation):
    require_postgres(economy_db)
    from app.economy import lock_state
    client = signed_client(economy_db)
    old = state(client)
    entered = Event()
    engine = economy_db.kw['bind']
    def before_execute(conn, cursor, statement, parameters, context, many):
        if 'FOR UPDATE' in statement:
            entered.set()
    with economy_db() as holder:
        current = lock_state(holder, 1)
        if operation == 'reward':
            contests._add_dandelion(holder, 1, 20)
        else:
            purchase_catalog_item(holder, profile_id=1, item_key='ladybug')
        expected = current.state_json['progress']['credits']
        event.listen(engine, 'before_cursor_execute', before_execute)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(client.put, '/account/state', json=old)
                try:
                    assert entered.wait(5)
                    assert not future.done()
                finally:
                    holder.commit()
                assert future.result(timeout=10).status_code == 409
        finally:
            event.remove(engine, 'before_cursor_execute', before_execute)
    assert balance(client) == expected


def test_postgres_cached_reward_reloads_after_other_transaction(economy_db):
    require_postgres(economy_db)
    client = signed_client(economy_db)
    with economy_db() as stale:
        cached = stale.get(WoodchuckState, 1)
        assert client.post('/account/daily-secret', json={'passcode':'union'}).status_code == 200
        before = balance(client)
        assert cached.state_json["progress"]["credits"] == before - 20
        contests._add_dandelion(stale, 1, 7)
        stale.commit()
    assert balance(client) == before + 7


def test_postgres_two_syncs_with_one_revision_have_one_winner(economy_db):
    require_postgres(economy_db)
    from threading import Barrier
    clients = [signed_client(economy_db), signed_client(economy_db)]
    snapshot = state(clients[0])
    snapshot['progress']['credits'] = 999999
    barrier = Barrier(2)
    def put(client):
        barrier.wait(timeout=5)
        return client.put('/account/state', json=snapshot).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(put, clients))
    assert sorted(results) == [200, 409]
    assert balance(clients[0]) == 101


def test_postgres_parallel_practice_cannot_exceed_daily_cap(economy_db):
    require_postgres(economy_db)
    from threading import Barrier
    clients = [signed_client(economy_db), signed_client(economy_db)]
    before = balance(clients[0])
    barrier = Barrier(2)
    def save(index):
        barrier.wait(timeout=5)
        return clients[index].post('/practice-charts', json={
            'practice_date': datetime.now(contests.CENTRAL).date().isoformat(),
            'minutes': 250, 'credits_awarded': 75, 'submission_key': f'parallel-{index}'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, (0, 1)))
    assert all(result.status_code == 201 for result in results)
    assert sorted(result.json()['chart']['credits_awarded'] for result in results) == [0, 0]
    assert balance(clients[0]) == before


def test_postgres_duplicate_reward_and_purchase_do_not_lose_updates(economy_db):
    require_postgres(economy_db)
    from threading import Barrier
    from app.store_catalog import ALL_ITEMS
    clients = [signed_client(economy_db) for _ in range(3)]
    before = balance(clients[0])
    barrier = Barrier(3)
    def act(index):
        barrier.wait(timeout=5)
        if index < 2:
            return clients[index].post('/account/daily-secret', json={'passcode': 'union'})
        return clients[index].post('/store/purchases', json={'item_key': 'ladybug'})
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(act, (0, 1, 2)))
    assert all(result.status_code in (200, 201) for result in results)
    assert sum(result.json().get('redeemed', False) for result in results) == 1
    assert balance(clients[0]) == before + 20 - ALL_ITEMS['ladybug'].price


def test_pristine_keeps_existing_zero_credit_policy(economy_db):
    client = signed_client(economy_db)
    before = balance(client)
    response = client.post('/practice-charts/pristine', json={
        'detected_playing_seconds': 300, 'submission_key': 'pristine-no-credit'})
    assert response.status_code == 201
    assert response.json()['chart']['credits_awarded'] == 0
    assert balance(client) == before


@pytest.mark.parametrize('legacy', [False, True])
def test_practice_cap_preserves_current_and_legacy_quest_rules(economy_db, legacy):
    client = signed_client(economy_db)
    before = balance(client)
    today = datetime.now(contests.CENTRAL).date().isoformat()
    challenge = client.get('/contests/bonus-challenge/current').json()['challenge']
    if legacy:
        result = client.post('/contests/quest/completions', json={
            'activity_date': today, 'quest_id': challenge['challenge_id'],
            'minutes': challenge['target_minutes'],
            'logged_minutes': challenge['target_minutes']})
    else:
        result = client.post('/contests/bonus-challenge/progress', json={
            'activity_date': today, 'challenge_instance': challenge['instance_key']})
    assert result.status_code == 409
    assert balance(client) == before
    result = client.post('/practice-charts', json={'practice_date': today,
        'minutes': 500, 'submission_key': 'after-quest', 'credits_awarded': 0})
    assert result.status_code == 201
    assert result.json()['chart']['credits_awarded'] == 0
    assert balance(client) == before
