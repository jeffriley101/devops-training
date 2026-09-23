"""Negative score/answer tests using disposable state; no real gameplay claims."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.arcade_rewards import answer_history_play, complete_arcade_play, start_arcade_play
from app.db import Base
from app.history_attempts import HISTORY_STATE_KEY, HistoryAttemptError
from app.history_mystery import history_mystery_questions_for_date
from app.main import app
from app.models import ArcadeHighScore, ArcadePlaySession, WoodchuckState
from tests.test_history_mystery import history_database, signed_client, add_profile
from tests.test_team_families import disposable_url


def start(client):
    result = client.post('/arcade/plays', json={'request_id': uuid4().hex, 'game_key': 'history-mystery'})
    assert result.status_code == 200
    return result.json()


def questions(factory, token):
    with factory() as s:
        play = s.scalar(select(ArcadePlaySession).where(ArcadePlaySession.play_token == token))
        return history_mystery_questions_for_date(play.daily_play_date)


def answer(client, token, index, choice):
    return client.post(f'/arcade/plays/{token}/answer', json={'question_index': index, 'choice': choice})


def saved(client):
    data = client.get('/account/state').json()
    state = data['state']
    state.setdefault('account', {})['serverRevision'] = data['revision']
    return state


@pytest.mark.parametrize('score', range(6))
def test_server_scores_real_choices_and_pays_existing_tiers(history_database, score):
    client, profile = signed_client(history_database, f'TIER{score}', credits=119)
    play = start(client)
    token = play['play_token']
    assert 'answer' not in play['history']['question']
    for index, question in enumerate(questions(history_database, token)):
        choice = question['answer'] if index < score else next(c for c in question['choices'] if c != question['answer'])
        result = answer(client, token, index, choice)
        assert result.status_code == 200
        assert result.json()['history']['score'] == min(index + 1, score)
    payload = result.json()
    assert payload['score'] == payload['best_score'] == score
    assert payload['payout'] == [0, 0, 0, 1, 2, 5][score]
    assert payload['balance'] == 19 + payload['payout']
    # Both completion endpoints are now harmless idempotent acknowledgments.
    for url, body in [(f'/arcade/plays/{token}/complete', {'score': score}),
                      ('/arcade/scores/history-mystery', {'score': score, 'play_token': token})]:
        replay = client.post(url, json=body)
        assert replay.status_code == 200 and replay.json()['already_completed']
        assert replay.json()['balance'] == payload['balance']
    assert client.post(f'/arcade/plays/{token}/complete', json={'score': (score + 1) % 6}).status_code == 409
    assert start_completed(client) == 409


def start_completed(client):
    return client.post('/arcade/plays', json={'request_id': uuid4().hex, 'game_key': 'history-mystery'}).status_code


def test_forged_score_alternate_endpoint_and_state_cannot_mint_reward(history_database):
    client, profile = signed_client(history_database, 'FORGE', credits=119)
    play = start(client)
    token = play['play_token']
    for endpoint, body in [(f'/arcade/plays/{token}/complete', {'score': 5}),
                           ('/arcade/scores/history-mystery', {'score': 5, 'play_token': token})]:
        assert client.post(endpoint, json=body).status_code == 409
    state = saved(client)
    original = deepcopy(state[HISTORY_STATE_KEY])
    state[HISTORY_STATE_KEY]['answers'] = [q['answer'] for q in questions(history_database, token)]
    state['progress']['credits'] = 999999
    assert client.put('/account/state', json=state).status_code == 200
    assert saved(client)[HISTORY_STATE_KEY] == original
    assert saved(client)['progress']['credits'] == 19
    assert client.post(f'/arcade/plays/{token}/complete', json={'score': 5}).status_code == 409
    with history_database() as s:
        assert s.scalar(select(ArcadeHighScore.id)) is None


def test_answer_order_retries_refresh_and_stale_sync(history_database):
    client, profile = signed_client(history_database, 'RETRY', credits=119)
    play = start(client)
    token = play['play_token']
    q = questions(history_database, token)
    stale = saved(client)
    assert answer(client, token, 1, q[1]['answer']).status_code == 409
    assert answer(client, token, 0, 'invented choice').status_code == 409
    accepted = answer(client, token, 0, q[0]['answer']).json()
    retry = answer(client, token, 0, q[0]['answer']).json()
    assert retry == accepted
    assert answer(client, token, 0, next(c for c in q[0]['choices'] if c != q[0]['answer'])).status_code == 409
    assert client.put('/account/state', json=stale).status_code == 409
    resumed = start(client)
    assert resumed['resumed'] and resumed['play_token'] == token
    assert resumed['balance'] == 19 and resumed['history']['question_index'] == 1
    assert client.get('/arcade/plays/status/history-mystery').json()['daily_play_resumable']
    # Page HTML no longer publishes today's answer key; state stores only committed choices.
    html = client.get('/arcade/history-mystery').text
    assert 'history-mystery-question-data' not in html
    assert len(saved(client)[HISTORY_STATE_KEY]['answers']) == 1


def test_identity_game_binding_and_unknown_tokens(history_database):
    a, _ = signed_client(history_database, 'OWNER')
    b, _ = signed_client(history_database, 'OTHER')
    token = start(a)['play_token']
    for client in (b, TestClient(app)):
        expected = 404 if client is b else 401
        assert answer(client, token, 0, 'guess').status_code == expected
        assert client.post(f'/arcade/plays/{token}/complete', json={'score': 5}).status_code == expected
    blue = a.post('/arcade/plays', json={'request_id': uuid4().hex, 'game_key': 'blue'}).json()['play_token']
    assert answer(a, blue, 0, 'guess').status_code == 404
    assert answer(a, 'synthetic-unknown-token', 0, 'guess').status_code == 404
    assert a.post('/arcade/scores/blue', json={'score': 5, 'play_token': token}).status_code == 409


@pytest.mark.parametrize('signature', [None, 'non-ascii-\u2603', '\ud800'])
def test_pre_release_browser_json_is_not_promoted_to_trusted_answers(history_database, signature):
    client, profile = signed_client(history_database, 'SEED')
    play = start(client)
    token = play['play_token']
    with history_database() as s:
        state = s.get(WoodchuckState, profile.id)
        payload = deepcopy(state.state_json)
        # Model arbitrary JSON persisted by the old generic state endpoint. Even
        # a captured valid capsule cannot be modified to add unearned answers.
        payload[HISTORY_STATE_KEY]['answers'] = [q['answer'] for q in questions(history_database, token)]
        if signature is not None:
            payload[HISTORY_STATE_KEY]['signature'] = signature
        state.state_json = payload
        s.commit()
    assert client.post(f'/arcade/plays/{token}/complete', json={'score': 5}).status_code == 409
    resumed = start(client)
    assert resumed['history']['score'] == 0 and resumed['history']['question_index'] == 0
    assert resumed['balance'] == 19


def test_old_signed_progress_cannot_roll_back_answers_through_sync(history_database):
    client, _ = signed_client(history_database, 'OLD')
    token = start(client)['play_token']
    before = saved(client)
    q = questions(history_database, token)
    assert answer(client, token, 0, q[0]['answer']).status_code == 200
    latest = saved(client)
    latest[HISTORY_STATE_KEY] = before[HISTORY_STATE_KEY]
    assert client.put('/account/state', json=latest).status_code == 200
    assert len(saved(client)[HISTORY_STATE_KEY]['answers']) == 1


def test_expiry_and_legacy_unfinished_refresh(history_database):
    client, profile = signed_client(history_database, 'EXPIRY')
    play = start(client)
    token = play['play_token']
    with history_database() as s:
        state = s.get(WoodchuckState, profile.id)
        state.state_json = {'progress': {'credits': 19}}  # Pre-release unfinished quiz.
        s.commit()
    resumed = start(client)
    assert resumed['resumed'] and resumed['balance'] == 19
    assert resumed['history']['question_index'] == 0
    with history_database() as s:
        row = s.scalar(select(ArcadePlaySession).where(ArcadePlaySession.play_token == token))
        row.daily_play_date -= timedelta(days=1)
        s.commit()
    assert answer(client, token, 0, 'guess').status_code == 409
    assert client.post(f'/arcade/plays/{token}/complete', json={'score': 5}).status_code == 409
    fresh = start(client)
    assert fresh['play_token'] != token and fresh['balance'] == 19


@pytest.fixture
def postgres_history(tmp_path):
    engine = create_engine(disposable_url(tmp_path, 'postgresql'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    profile = add_profile(factory, 'PG', credits=119)
    yield factory, profile
    engine.dispose()


def test_postgres_concurrent_start_charges_once(postgres_history):
    factory, profile = postgres_history
    barrier = Barrier(4)
    def run():
        with factory() as s:
            barrier.wait(timeout=10)
            result = start_arcade_play(s, profile_id=profile.id, game_key='history-mystery')
            s.commit()
            return result.play.play_token, result.balance
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: run(), range(4)))
    assert len(set(results)) == 1 and results[0][1] == 19


def test_postgres_concurrent_last_answer_and_completion_pay_once(postgres_history):
    factory, profile = postgres_history
    with factory() as s:
        play = start_arcade_play(s, profile_id=profile.id, game_key='history-mystery').play
        s.commit()
    q = questions(factory, play.play_token)
    with factory() as s:
        for index in range(4):
            answer_history_play(s, profile_id=profile.id, play_token=play.play_token,
                                question_index=index, choice=q[index]['answer'])
        s.commit()
    barrier = Barrier(6)
    def run(index):
        with factory() as s:
            barrier.wait(timeout=10)
            try:
                result = (answer_history_play(s, profile_id=profile.id, play_token=play.play_token,
                           question_index=4, choice=q[4]['answer']) if index < 4 else
                          complete_arcade_play(s, profile_id=profile.id, play_token=play.play_token, score=5))
                s.commit()
                return result['balance']
            except HistoryAttemptError:
                s.rollback()
                return 'not-yet-finished'
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(run, range(6)))
    assert all(value in (24, 'not-yet-finished') for value in results)
    with factory() as s:
        assert s.get(WoodchuckState, profile.id).state_json['progress']['credits'] == 24
        assert s.scalar(select(func.count(ArcadeHighScore.id))) == 1
        assert s.scalar(select(ArcadePlaySession)).payout == 5


def test_postgres_concurrent_conflicting_answers_choose_once(postgres_history):
    factory, profile = postgres_history
    with factory() as s:
        play = start_arcade_play(s, profile_id=profile.id, game_key='history-mystery').play
        s.commit()
    q = questions(factory, play.play_token)[0]
    barrier = Barrier(2)
    def run(choice):
        with factory() as s:
            barrier.wait(timeout=10)
            try:
                answer_history_play(s, profile_id=profile.id, play_token=play.play_token,
                                    question_index=0, choice=choice)
                s.commit()
                return 'accepted'
            except HistoryAttemptError:
                s.rollback()
                return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(run, q['choices'][:2]))
    assert sorted(results) == ['accepted', 'conflict']
    with factory() as s:
        assert len(s.get(WoodchuckState, profile.id).state_json[HISTORY_STATE_KEY]['answers']) == 1
