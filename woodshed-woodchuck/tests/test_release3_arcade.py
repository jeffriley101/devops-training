"""R3 transaction, retry, access and public-score contracts on local databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import Base
from app import arcade_access as access, arcade_rewards as arcade
from app.age_privacy import declare_age
from app.age_models import AccountPrivacy
from app.models import (ArcadeAttemptPack, ArcadeStartRequest, ArcadePlaySession,
                        ArcadeHighScore, WoodchuckProfile, WoodchuckState)
from app.security import hash_pin
from app.tester_enrollments import enroll_tester
from tests.test_team_families import disposable_url

NOW = datetime.now(timezone.utc)


@pytest.fixture(params=['sqlite', 'postgresql'])
def db(request, tmp_path, monkeypatch):
    engine = (create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
              if request.param == 'sqlite' else create_engine(disposable_url(tmp_path, 'postgresql')))
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    with factory() as s:
        pin = hash_pin('2468')
        for i in range(1, 9):
            p = WoodchuckProfile(id=i, woodchuck_id=f'WC-R3-{i}', display_name=f'Player {i}',
                pin_hash=pin, instrument='Flute', level='Beginner', goal='Practice')
            s.add(p); s.flush()
            declare_age(s, i, 'adult', at=NOW-timedelta(days=10))
            s.add(WoodchuckState(profile_id=i, revision=0, state_json={'progress': {'credits': 100}}))
        s.commit()
    yield factory
    engine.dispose()


def count(s, model):
    return s.scalar(select(func.count()).select_from(model))


def start(db, game='thirds', key=None, pid=1, now=None):
    with db() as s:
        result = arcade.start_arcade_play(s, profile_id=pid, game_key=game, request_id=key or uuid4().hex, now=now)
        s.commit()
        return result


def complete(db, play, score=0, now=None):
    with db() as s:
        result = arcade.complete_arcade_play(s, profile_id=play.profile_id, play_token=play.play_token, score=score, now=now)
        s.commit()
        return result


def test_exact_three_attempts_no_expiry_reload_retry_and_exhaustion(db):
    with db() as s:
        status = arcade.arcade_play_status(s, profile_id=1, game_key='thirds')
        assert status['balance'] == 100 and status['attempts_remaining'] == 0
        assert count(s, ArcadeAttemptPack) == count(s, ArcadePlaySession) == 0
    key = uuid4().hex
    first = start(db, key=key)
    assert first.balance == 0 and first.play.entry_cost == 100
    other_tab = start(db)
    assert other_tab.play.id == first.play.id and other_tab.resumed
    complete(db, first.play)
    # A response lost until after completion must never authorize a new attempt.
    assert start(db, key=key).play.id == first.play.id
    for attempt in (2, 3):
        play = start(db, now=NOW+timedelta(days=365))
        assert play.play.attempt_number == attempt and play.play.entry_cost == 0
        complete(db, play.play, now=NOW+timedelta(days=365))
    with pytest.raises(arcade.InsufficientArcadeBalanceError):
        start(db)
    with db() as s:
        assert count(s, ArcadeAttemptPack) == 1 and count(s, ArcadePlaySession) == 3
        assert s.scalar(select(ArcadeAttemptPack)).attempts_used == 3
        assert arcade.remaining_attempts(s, 1, 'thirds') == 0
        assert s.get(WoodchuckState, 1).state_json['progress']['credits'] == 0


def test_insufficient_and_failed_transaction_leave_no_purchase(db, monkeypatch):
    with db() as s:
        s.get(WoodchuckState, 1).state_json = {'progress': {'credits': 99}}; s.commit()
    with pytest.raises(arcade.InsufficientArcadeBalanceError): start(db)
    with db() as s:
        assert count(s, ArcadeAttemptPack) == count(s, ArcadePlaySession) == count(s, ArcadeStartRequest) == 0
        s.get(WoodchuckState, 1).state_json = {'progress': {'credits': 100}}; s.commit()
    # Failure after debit and attempt insert still rolls the entire transaction back.
    monkeypatch.setattr(arcade, 'initialize_history', lambda *_: (_ for _ in ()).throw(RuntimeError('injected failure')))
    with pytest.raises(RuntimeError): start(db, 'history-mystery')
    with db() as s:
        assert count(s, ArcadeAttemptPack) == count(s, ArcadePlaySession) == 0
        assert s.get(WoodchuckState, 1).state_json['progress']['credits'] == 100


@pytest.mark.parametrize('game', ['blue', 'plunge-burrow'])
def test_always_free_and_daily_rewards_still_capped(db, game):
    with db() as s:
        s.get(WoodchuckState, 1).state_json = {'progress': {'credits': 0}}; s.commit()
    for n in range(11):
        run = start(db, game)
        assert run.play.entry_cost == 0
        result = complete(db, run.play, 250)
        assert result['payout'] == 0
        assert complete(db, run.play, 250)['already_completed']
    with db() as s:
        assert count(s, ArcadeAttemptPack) == 0
        assert s.get(WoodchuckState, 1).state_json['progress']['credits'] == 0


@pytest.mark.parametrize('kind', ['PILOT-D1', 'C001', 'membership'])
def test_real_full_entitlement_free_normal_games_but_not_classroom(db, kind):
    with db() as s:
        if kind == 'membership':
            from app.memberships import Actor, create_complimentary_membership
            create_complimentary_membership(s, Actor('student', 1), Actor('admin'))
        else: enroll_tester(s, 1, kind, NOW-timedelta(days=1))
        s.get(WoodchuckState, 1).state_json = {'progress': {'credits': 0}}; s.commit()
    for _ in range(4):
        run = start(db); assert run.play.entry_cost == 0
        complete(db, run.play)
    for game in access.NORMAL - {'thirds'}:
        assert start(db, game).play.entry_cost == 0
    with db() as s:
        assert count(s, ArcadeAttemptPack) == 0
        assert access.access_policy(s, 1, 'thirds')['free_reason'] == 'full_access'
        for key in access.CLASSROOM:
            assert access.access_policy(s, 1, key)['allowed'] is False
        s.get(AccountPrivacy, 1).age_band = 'under13'; s.commit()
    from app.age_privacy import AgeScreenRequired
    with pytest.raises(AgeScreenRequired): start(db)


def test_classroom_policy_is_pluggable_but_no_fake_game_routes(db, monkeypatch):
    assert len(access.CLASSROOM) == 5
    with db() as s:
        for key in access.CLASSROOM: assert not access.access_policy(s, 1, key)['allowed']
        monkeypatch.setattr(access, 'classroom_authorized', lambda session, pid: pid == 1)
        for key in access.CLASSROOM:
            assert access.access_policy(s, 1, key)['free_reason'] == 'classroom'
            assert access.access_policy(s, 1, key)['entry_cost'] == 0
            assert not access.access_policy(s, 2, key)['allowed']
            with pytest.raises(ValueError): arcade.start_arcade_play(s, profile_id=1, game_key=key)


def test_http_mismatched_game_direct_score_and_replayed_completion(db):
    c = TestClient(app)
    assert c.post('/account/login', data={'woodchuck_id': 'WC-R3-1', 'pin': '2468'}).status_code == 200
    assert c.post('/arcade/plays', json={'game_key': 'blue'}).status_code == 422
    key = uuid4().hex
    play = c.post('/arcade/plays', json={'game_key': 'thirds', 'request_id': key}).json()
    token = play['play_token']
    assert c.post('/arcade/scores/thirds', json={'score': 10}).status_code == 422
    assert c.post('/arcade/scores/blue', json={'score': 10, 'play_token': token}).status_code == 409
    assert c.post('/arcade/plays', json={'game_key': 'blue', 'request_id': key}).status_code == 409
    first = c.post(f'/arcade/plays/{token}/complete', json={'score': 10}).json()
    duplicate = c.post(f'/arcade/plays/{token}/complete', json={'score': 10}).json()
    assert first['balance'] == duplicate['balance'] and duplicate['already_completed']
    assert c.post(f'/arcade/plays/{token}/complete', json={'score': 11}).status_code == 409
    with db() as s:
        assert count(s, ArcadeHighScore) == 0
        assert count(s, ArcadePlaySession) == 1


def test_history_pack_carries_days_without_changing_daily_limit(db):
    first = start(db, 'history-mystery', now=NOW)
    assert start(db, 'history-mystery', now=NOW).play.id == first.play.id
    for day in (1, 2):
        run = start(db, 'history-mystery', now=NOW+timedelta(days=day))
        assert run.play.entry_cost == 0 and run.play.attempt_number == day+1
    with pytest.raises(arcade.InsufficientArcadeBalanceError): start(db, 'history-mystery', now=NOW+timedelta(days=3))
    with db() as s:
        assert count(s, ArcadeAttemptPack) == 1
        with pytest.raises(ValueError): arcade.complete_arcade_play(s, profile_id=1, play_token=first.play.play_token, score=0, now=NOW+timedelta(days=4))


@pytest.mark.parametrize('game', ['blue', 'plunge-burrow'])
def test_shared_top5_ties_private_self_and_lower_new_public_best(db, game):
    from app.arcade_scores import arcade_score_payload
    from app.xp import plunge_best_payload
    def scores(s, pid):
        return plunge_best_payload(s, profile_id=pid) if game == 'plunge-burrow' else arcade_score_payload(s, profile_id=pid, game_key=game)
    for pid, score in [(1, 1000), (2, 90), (3, 90), (4, 80), (5, 70), (6, 60), (7, 999)]:
        complete(db, start(db, game, pid=pid).play, score)
    with db() as s:
        # Recorded best 1000 predates a protected new publication boundary.
        rule = s.get(AccountPrivacy, 1); rule.public_from = NOW+timedelta(days=1); rule.private_plunge_best = 1000
        s.get(AccountPrivacy, 7).age_band = 'under13'; s.get(AccountPrivacy, 7).public_from = None
        s.commit()
    complete(db, start(db, game, pid=1, now=NOW+timedelta(days=2)).play, 95, now=NOW+timedelta(days=2))
    with db() as s:
        shared = scores(s, 8)['leaderboard']
        assert shared == []
        for pid in (1, 2, 6, 7):
            assert scores(s, pid)['best_score'] == 0
            assert scores(s, pid)['leaderboard'] == []


@pytest.mark.parametrize('game', ['blue', 'plunge-burrow'])
def test_private_start_completed_after_public_transition_stays_private(db, game):
    from app.age_privacy import can_publish
    from app.arcade_scores import arcade_score_payload
    from app.xp import plunge_best_payload

    def scores(s, pid):
        return (plunge_best_payload(s, profile_id=pid) if game == 'plunge-burrow'
                else arcade_score_payload(s, profile_id=pid, game_key=game))

    with db() as s:
        s.get(AccountPrivacy, 1).public_from = None
        s.commit()
        assert not can_publish(s, 1)
    private = start(db, game, now=NOW-timedelta(days=2)).play
    with db() as s:
        rule = s.get(AccountPrivacy, 1)
        rule.public_from = NOW-timedelta(days=1)
        rule.private_plunge_best = s.get(WoodchuckProfile, 1).plunge_best_score
        s.commit()
        assert can_publish(s, 1) and not can_publish(s, 1, at=private.started_at)
    complete(db, private, 1000, now=NOW)
    complete(db, start(db, game, pid=2, now=NOW).play, 90, now=NOW)
    with db() as s:
        assert scores(s, 8)['leaderboard'] == []
        own = scores(s, 1)
        assert own['best_score'] == 0
        assert own['leaderboard'] == []

    # A lower public result must be selected even though the aggregate stays 1000.
    for score in (95, 80):
        complete(db, start(db, game, now=NOW+timedelta(hours=1)).play, score,
                 now=NOW+timedelta(hours=1))
    with db() as s:
        shared = scores(s, 8)['leaderboard']
        assert shared == []
        assert scores(s, 1)['best_score'] == 0
        assert scores(s, 1)['leaderboard'] == []


@pytest.mark.parametrize('game', ['blue', 'plunge-burrow'])
@pytest.mark.parametrize('provenance', ['missing', 'unfinished'])
def test_aggregate_without_completed_attempt_provenance_is_self_only(db, game, provenance):
    from app.arcade_scores import arcade_score_payload, record_arcade_high_score
    from app.xp import plunge_best_payload, record_plunge_best_score

    with db() as s:
        if game == 'plunge-burrow':
            record_plunge_best_score(s, profile_id=1, score=1000)
        else:
            record_arcade_high_score(s, profile_id=1, game_key=game, score=1000)
        if provenance == 'unfinished':
            s.add(ArcadePlaySession(profile_id=1, game_key=game, play_token=uuid4().hex,
                                   started_at=NOW, submitted_score=1000, entry_cost=1))
        s.commit()
        def scores(pid):
            return (plunge_best_payload(s, profile_id=pid) if game == 'plunge-burrow'
                    else arcade_score_payload(s, profile_id=pid, game_key=game))
        assert scores(8)['leaderboard'] == []
        assert scores(1)['best_score'] == 1000
        assert scores(1)['leaderboard'] == [dict(rank=1, display_name='Player 1', score=1000, is_current_user=True)]


def test_concurrent_tabs_and_duplicate_completions(db):
    if db.kw['bind'].dialect.name != 'postgresql': pytest.skip('Row-lock concurrency requires PostgreSQL')
    barrier = Barrier(4)
    keys = [uuid4().hex for _ in range(4)]
    def worker(key):
        barrier.wait(); return start(db, key=key)
    with ThreadPoolExecutor(max_workers=4) as pool: runs = list(pool.map(worker, keys))
    assert len({r.play.id for r in runs}) == 1
    def finish(_): return complete(db, runs[0].play, 12)
    with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(finish, range(4)))
    assert sum(not r['already_completed'] for r in results) == 1
    for key in keys: assert start(db, key=key).play.id == runs[0].play.id
    with db() as s:
        assert count(s, ArcadeAttemptPack) == count(s, ArcadePlaySession) == 1
        assert count(s, ArcadeHighScore) == 0
        assert s.get(WoodchuckState, 1).state_json['progress']['credits'] == 0
        assert arcade.remaining_attempts(s, 1, 'thirds') == 2
