"""Synthetic accounts, actual HTTP actions, and disposable PostgreSQL lock tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sys
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import arcade_rewards, arcade_routes
from app.arcade_challenges import GAMES, ChallengeError
from app.arcade_rewards import action_arcade_play, complete_arcade_play
from app.arcade_scores import arcade_score_payload
from app.age_models import AccountPrivacy
from app.db import Base
from app.models import ArcadeHighScore, ArcadePlaySession, WoodchuckProfile
from tests.test_arcade_economy import signed_client, add_player
from tests.test_team_families import disposable_url


@pytest.fixture(params=['sqlite', 'postgresql'])
def db(request, tmp_path, monkeypatch):
    engine = (create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
              if request.param == 'sqlite' else create_engine(disposable_url(tmp_path, 'postgresql')))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    yield factory
    engine.dispose()


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = datetime.now(timezone.utc)
        def advance(self, seconds=1):
            self.now += timedelta(seconds=seconds)
    value = Clock()
    monkeypatch.setattr(arcade_rewards, '_utc_now', lambda now: now or value.now)
    monkeypatch.setattr(arcade_routes, '_utc_now', lambda now: now or value.now)
    return value


def start(client, game):
    response = client.post('/arcade/plays', json={'game_key': game, 'request_id': uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def play_row(db, token):
    with db() as session:
        return session.scalar(select(ArcadePlaySession).where(ArcadePlaySession.play_token == token))


def correct_answer(play):
    state = play.challenge_state
    question = state['question']
    return (question['rootMidi'] + question['notes'][state['note_index']][0]
            if play.game_key == 'scale-keyboard' else question['answer'])


def action(client, token, index, answer):
    return client.post(f'/arcade/plays/{token}/action', json={'action_index': index, 'answer': answer})


def finish(client, token, score):
    return client.post(f'/arcade/plays/{token}/complete', json={'score': score})


@pytest.mark.parametrize('game', sorted(GAMES))
def test_security_and_multi_account_public_top5(db, clock, game):
    a, profile = signed_client(db, 'A', credits=500)
    b, _ = signed_client(db, 'B')
    opened = start(a, game)
    token = opened['play_token']
    assert opened['result_authority'] == 'server_scored'
    assert 'answer' not in opened['challenge']['question']
    # No token, arbitrary final totals, impossible immediate completion, and aliases.
    assert a.post(f'/arcade/scores/{game}', json={'score':2147483647}).status_code == 422
    for score in (0, 1, 2147483647):
        assert finish(a, token, score).status_code == 409
        assert a.post(f'/arcade/scores/{game}', json={'score':score, 'play_token':token}).status_code == 409
    clock.advance()
    answer = correct_answer(play_row(db, token))
    assert action(b, token, 0, answer).status_code == 404
    assert finish(b, token, 0).status_code == 404
    assert action(a, token, 1, answer).status_code == 409
    for invalid in ('invalid', True, None, {}, 999999999):
        assert action(a, token, 0, invalid).status_code in (409, 422)
    assert play_row(db, token).challenge_state['score'] == 0
    result = action(a, token, 0, answer)
    assert result.status_code == 200, result.text
    score = 100 if game == 'scale-keyboard' else 1
    assert result.json()['challenge']['score'] == score
    assert action(a, token, 0, answer).json() == result.json()
    assert action(a, token, 0, 'A' if answer != 'A' else 'B').status_code == 409
    assert action(a, token, 1, correct_answer(play_row(db, token))).status_code == 409  # impossible rate
    clock.advance(32)
    assert action(a, token, 1, correct_answer(play_row(db, token))).status_code == 409
    assert finish(a, token, 2147483647).status_code == 409
    result = finish(a, token, score)
    assert result.status_code == 200, result.text
    assert result.json()['payout'] == 0
    assert result.json()['result_authority'] == 'server_scored'
    saved = play_row(db, token)
    assert saved.authoritative_score == score and saved.completed_at is not None
    board = b.get(f'/arcade/scores/{game}').json()['leaderboard']
    assert [(r['display_name'], r['score']) for r in board] == [(profile.display_name, score)]
    assert b.get(f'/arcade/{game}').status_code == 200
    assert finish(a, token, score).json()['already_completed'] is True
    assert finish(a, token, score+1).status_code == 409
    assert action(a, token, 1, correct_answer(saved)).status_code == 409
    assert b.get(f'/arcade/scores/{game}').json()['leaderboard'] == board


@pytest.mark.parametrize('game', sorted(GAMES))
def test_wrong_answers_and_authoritative_scoring(db, clock, game):
    client, _ = signed_client(db, 'SCORING')
    token = start(client, game)['play_token']
    def send(answer):
        clock.advance()
        play = play_row(db, token)
        result = action(client, token, play.challenge_state['index'], answer)
        assert result.status_code == 200, result.text
        return result.json()['challenge']
    if game == 'scale-keyboard':
        question = play_row(db, token).challenge_state['question']
        assert send(question['rootMidi'] + 1)['score'] == 0
        assert send(question['rootMidi'])['score'] == 100
        assert send(question['rootMidi'] + 1)['score'] == 50
        for _ in range(7):
            state = send(correct_answer(play_row(db, token)))
        assert state['score'] == 1250 and state['note_index'] == 0
        assert state['question']['key'] != question['key']
    else:
        assert send(correct_answer(play_row(db, token)))['score'] == 1
        for _ in range(2):
            answer = correct_answer(play_row(db, token))
            wrong = ('2nd' if answer != '2nd' else '3rd') if game == 'interval-basic-training' else ('A' if answer != 'A' else 'B')
            state = send(wrong)
            assert state['score'] == 1
        if game == 'interval-basic-training':
            assert state['finished'] and state['mistakes'] == 2
            assert action(client, token, 3, '2nd').status_code == 409
            assert finish(client, token, 1).status_code == 200
            return
    clock.advance(30)
    assert finish(client, token, state['score']).status_code == 200


@pytest.mark.parametrize('game', sorted(GAMES))
def test_public_projection_privacy_ties_and_legacy(db, clock, game):
    client, owner = signed_client(db, 'OWNER')
    viewer, observer = signed_client(db, 'VIEWER')
    token = start(client, game)['play_token']
    clock.advance()
    assert action(client, token, 0, correct_answer(play_row(db, token))).status_code == 200
    clock.advance(30)
    score = 100 if game == 'scale-keyboard' else 1
    assert finish(client, token, score).status_code == 200
    # Both timing boundaries and current age/privacy/status apply even to one's own Top 5.
    with db() as session:
        rule = session.get(AccountPrivacy, owner.id)
        original = rule.public_from
        rule.public_from = clock.now - timedelta(seconds=1)
        session.commit()
    assert viewer.get(f'/arcade/scores/{game}').json()['leaderboard'] == []
    assert client.get(f'/arcade/scores/{game}').json()['best_score'] == score
    for band, public_from, status in [('under13', original, 'active'), ('adult', None, 'active'),
                                      ('adult', original, 'deleted')]:
        with db() as session:
            rule = session.get(AccountPrivacy, owner.id)
            rule.age_band, rule.public_from = band, public_from
            session.get(WoodchuckProfile, owner.id).status = status
            session.commit()
        assert viewer.get(f'/arcade/scores/{game}').json()['leaderboard'] == []
    # Seed projection-only cases explicitly; these aren't proof of legitimate gameplay.
    for i, value in enumerate([90, 90, 80, 70, 60, 50]):
        profile = add_player(db, str(i))
        with db() as session:
            session.add(ArcadePlaySession(profile_id=profile.id, game_key=game, play_token=uuid4().hex,
                started_at=clock.now-timedelta(seconds=30), completed_at=clock.now,
                submitted_score=value, authoritative_score=value, entry_cost=0, payout=0))
            session.commit()
    with db() as session:
        session.add(ArcadePlaySession(profile_id=observer.id, game_key=game, play_token=uuid4().hex,
            started_at=clock.now-timedelta(seconds=30), completed_at=clock.now,
            submitted_score=2147483647, entry_cost=0, payout=0))
        session.add(ArcadeHighScore(profile_id=observer.id, game_key=game, best_score=2147483647))
        session.commit()
    payload = viewer.get(f'/arcade/scores/{game}').json()
    assert payload['best_score'] == 2147483647  # separate historical personal best only
    assert [r['score'] for r in payload['leaderboard']] == [90, 90, 80, 70, 60]
    assert [r['rank'] for r in payload['leaderboard']] == [1, 1, 3, 4, 5]
    assert all(r['display_name'] not in (owner.display_name, observer.display_name) for r in payload['leaderboard'])


def test_blue_remains_self_reported(db):
    a, _ = signed_client(db, 'BLUE')
    b, _ = signed_client(db, 'VIEWER')
    token = start(a, 'blue')['play_token']
    assert finish(a, token, 2147483647).json()['result_authority'] == 'self_reported'
    assert play_row(db, token).authoritative_score is None
    assert b.get('/arcade/scores/blue').json()['leaderboard'] == []


@pytest.mark.parametrize('game', sorted(GAMES))
def test_postgres_concurrent_actions_and_completion(db, clock, game):
    if db.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    client, profile = signed_client(db, 'RACE')
    token = start(client, game)['play_token']
    clock.advance()
    answer = correct_answer(play_row(db, token))
    barrier = Barrier(2)
    def submit(_):
        with db() as session:
            barrier.wait(timeout=5)
            value = action_arcade_play(session, profile_id=profile.id, play_token=token,
                                       action_index=0, answer=answer, now=clock.now)
            session.commit()
            return value['challenge']['score']
    score = 100 if game == 'scale-keyboard' else 1
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(submit, range(2))) == [score, score]
    clock.advance(30)
    barrier = Barrier(2)
    def complete(_):
        with db() as session:
            barrier.wait(timeout=5)
            value = complete_arcade_play(session, profile_id=profile.id, play_token=token,
                                         score=score, now=clock.now)
            session.commit()
            return value['already_completed']
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(complete, range(2))) == [False, True]
    assert play_row(db, token).authoritative_score == score


@pytest.mark.parametrize('game', sorted(GAMES))
def test_resume_expiry_legacy_and_private_best(db, clock, game):
    client, profile = signed_client(db, 'RESUME')
    viewer, _ = signed_client(db, 'WATCH')
    key = uuid4().hex
    first = client.post('/arcade/plays', json={'game_key':game, 'request_id':key}).json()
    token = first['play_token']
    assert client.post('/arcade/plays', json={'game_key':game, 'request_id':key}).json()['challenge'] == first['challenge']
    clock.advance()
    assert action(client, token, 0, correct_answer(play_row(db, token))).status_code == 200
    resumed = start(client, game)
    assert resumed['play_token'] == token and resumed['challenge']['action_index'] == 1
    assert resumed['charged_now'] == 0 and resumed['challenge']['remaining_ms'] == 29000
    # A larger lifetime aggregate/private claim cannot mask the legitimate public best.
    with db() as session:
        session.add(ArcadeHighScore(profile_id=profile.id, game_key=game, best_score=999999))
        session.commit()
    clock.advance(32)
    next_play = start(client, game)
    assert next_play['play_token'] != token and next_play['charged_now'] == 0
    score = 100 if game == 'scale-keyboard' else 1
    assert play_row(db, token).authoritative_score == score
    assert viewer.get(f'/arcade/scores/{game}').json()['leaderboard'][0]['score'] == score
    assert client.get(f'/arcade/scores/{game}').json()['best_score'] == 999999
    # Current revoked consent still removes an otherwise adult/public profile.
    from app.child_models import ConsentEvidence
    with db() as session:
        consent = ConsentEvidence(profile_id=profile.id, parent_email='synthetic@example.test',
            notice_version='test', notice_sha256='0'*64, approved_at=clock.now, confirmed_at=clock.now,
            withdrawn_at=clock.now)
        session.add(consent); session.flush()
        session.get(AccountPrivacy, profile.id).consent_id = consent.id
        session.commit()
    assert viewer.get(f'/arcade/scores/{game}').json()['leaderboard'] == []
    with db() as session:
        session.get(AccountPrivacy, profile.id).consent_id = None
        legacy = session.scalar(select(ArcadePlaySession).where(ArcadePlaySession.play_token == next_play['play_token']))
        legacy.challenge_state = None
        legacy.submitted_score = 2147483647
        session.commit()
    assert finish(client, next_play['play_token'], 2147483647).status_code == 409
    replacement = start(client, game)
    assert replacement['play_token'] != next_play['play_token']
    assert play_row(db, next_play['play_token']).authoritative_score is None


def test_history_uses_verified_evidence_and_never_promotes_legacy_claims(db):
    from app.history_mystery import HISTORY_MYSTERY_QUESTIONS
    from app.models import WoodchuckState
    from copy import deepcopy
    client, profile = signed_client(db, 'HISTORY')
    viewer, _ = signed_client(db, 'WATCH')
    result = start(client, 'history-mystery')
    token = result['play_token']
    answers = {q['id']:q['answer'] for q in HISTORY_MYSTERY_QUESTIONS}
    for index in range(5):
        result = client.post(f'/arcade/plays/{token}/answer', json={
            'question_index':index, 'choice':answers[result['history']['question']['id']]}).json()
    assert play_row(db, token).authoritative_score == 5
    assert viewer.get('/arcade/scores/history-mystery').json()['leaderboard'][0]['score'] == 5
    with db() as session:
        session.scalar(select(ArcadePlaySession).where(ArcadePlaySession.play_token == token)).authoritative_score = None
        session.commit()
    assert viewer.get('/arcade/scores/history-mystery').json()['leaderboard'][0]['score'] == 5
    with db() as session:
        state = session.get(WoodchuckState, profile.id)
        altered = deepcopy(state.state_json)
        altered['_history_mystery']['answers'][0] = 'fabricated'
        state.state_json = altered
        session.commit()
    assert viewer.get('/arcade/scores/history-mystery').json()['leaderboard'] == []


@pytest.mark.parametrize('game', sorted(GAMES))
def test_postgres_action_racing_completion_is_serialized(db, clock, game):
    if db.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    client, profile = signed_client(db, 'RACE2')
    token = start(client, game)['play_token']
    clock.advance(31)
    answer = correct_answer(play_row(db, token))
    barrier = Barrier(2)
    def race(kind):
        with db() as session:
            barrier.wait(timeout=5)
            try:
                if kind == 'action':
                    action_arcade_play(session, profile_id=profile.id, play_token=token,
                                       action_index=0, answer=answer, now=clock.now)
                else:
                    complete_arcade_play(session, profile_id=profile.id, play_token=token, score=0, now=clock.now)
                session.commit()
                return kind
            except ChallengeError:
                session.rollback()
                return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(race, ['action','complete']))
    saved = play_row(db, token)
    if 'complete' in results:
        assert results == [None, 'complete']
        assert saved.authoritative_score == saved.challenge_state['score'] == 0
    else:
        assert results == ['action', None]
        assert saved.authoritative_score is None and saved.completed_at is None
        assert finish(client, token, saved.challenge_state['score']).status_code == 200


@pytest.mark.parametrize('game', sorted(GAMES))
def test_completion_clock_tolerance_is_bounded(db, clock, game):
    client, _ = signed_client(db, 'CLOCK')
    token = start(client, game)['play_token']
    clock.advance(28)
    assert finish(client, token, 0).status_code == 409
    clock.advance(1)
    assert finish(client, token, 0).status_code == 200
    assert play_row(db, token).authoritative_score == 0
