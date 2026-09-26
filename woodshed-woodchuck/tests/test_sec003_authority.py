"""SEC-003: HTTP assertions cannot become earning or competitive evidence."""
from datetime import datetime, timedelta, timezone
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import contests, main
from app.models import (PracticeChart, PracticeChartVerification, CampPointAward,
                        RewardGrant, TrustedVerifier, StudentVerifierConnection)
from app.practice_charts import respond_to_practice_chart_verification
from app.xp import xp_sources
from tests.test_arcade_economy import economy_database, signed_client, balance


@pytest.fixture
def authority_db(economy_database, monkeypatch):
    # Include revocation middleware and every loaded route; no external DB.
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', economy_database)
    return economy_database


def today():
    return datetime.now(contests.CENTRAL).date().isoformat()


def book(**overrides):
    return dict(practice_date=today(), minutes=20, submission_key=uuid4().hex,
                **overrides)


def test_practice_self_reports_are_private_evidence_not_earning(authority_db):
    client, profile = signed_client(authority_db, 'PRACTICE')
    before = balance(authority_db, profile.id)
    payload = book()
    first = client.post('/practice-charts', json=payload)
    assert first.status_code == 201, first.text
    assert first.json()['chart']['authority'] == 'self_reported'
    assert first.json()['chart']['credits_awarded'] == 0
    for _ in range(3):
        assert client.post('/practice-charts', json=payload).json()['created'] is False
    assert client.post('/practice-charts', json={**payload, 'minutes':21}).status_code == 400
    for key in ('microphone-1', 'microphone-2'):
        result = client.post('/practice-charts/pristine', json={
            'detected_playing_seconds':3600, 'submission_key':key})
        assert result.status_code == 201, result.text
        assert result.json()['chart']['include_contests'] is False
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(PracticeChart)) == 3
        sources = xp_sources(session, profile_id=profile.id)
        assert sources['practice_minutes'] == sources['p_charts'] == 0
        from app.practice_duration import qualified_practice_clause
        assert session.scalar(select(func.count()).select_from(PracticeChart).where(qualified_practice_clause())) == 0
    assert balance(authority_db, profile.id) == before


@pytest.mark.parametrize('changes', [
    {'minutes':-1}, {'minutes':0}, {'minutes':1441}, {'minutes':10**15},
    {'minutes':1.5}, {'minutes':'20'}, {'minutes':True},
    {'submission_key':None}, {'submission_key':''}, {'submission_key':' '},
    {'submission_key':'x'*65}, {'practice_date':'1900-01-01'},
    {'practice_date':(datetime.now(contests.CENTRAL).date()+timedelta(days=1)).isoformat()},
    {'credits_awarded':10**10}, {'source':'approved'},
])
def test_practice_invalid_inputs_cannot_persist(authority_db, changes):
    client, profile = signed_client(authority_db, 'BOUNDS')
    assert client.post('/practice-charts', json={**book(), **changes}).status_code in (400,422)
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(PracticeChart)) == 0


def test_shared_book_pristine_duration_budget(authority_db):
    client, _ = signed_client(authority_db, 'BUDGET')
    assert client.post('/practice-charts', json={**book(), 'minutes':1440}).status_code == 201
    assert client.post('/practice-charts/pristine', json={
        'detected_playing_seconds':1, 'submission_key':'overflow'}).status_code == 400
    assert client.post('/practice-charts', json={**book(),
        'practice_date':(datetime.now(contests.CENTRAL).date()-timedelta(days=1)).isoformat()}).status_code == 400


@pytest.mark.parametrize('activity', ['hours','care','marching'])
def test_board_assertions_never_award(authority_db, activity):
    client, profile = signed_client(authority_db, 'BOARD')
    before = balance(authority_db, profile.id)
    for _ in range(3):
        result = client.post('/contests/camp-points/awards', json={
            'activity_type':activity, 'activity_date':today()})
        assert result.status_code == 400, result.text
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0
    assert balance(authority_db, profile.id) == before


def test_independent_review_qualifies_book_once_at_existing_rate(authority_db):
    client, profile = signed_client(authority_db, 'REVIEW')
    before = balance(authority_db, profile.id)
    from app.security import hash_pin
    with authority_db() as session:
        reviewer = TrustedVerifier(email='sec003@example.test', display_name='Reviewer', pin_hash=hash_pin('2468'))
        session.add(reviewer); session.flush()
        session.add(StudentVerifierConnection(profile_id=profile.id, verifier_id=reviewer.id,
            role='verifier', status='accepted'))
        session.commit()
        reviewer_id = reviewer.id
    response = client.post('/practice-charts', json=book(verifier_id=reviewer_id))
    assert response.status_code == 201, response.text
    verification_id = response.json()['chart']['verification']['id']
    assert balance(authority_db, profile.id) == before
    with authority_db() as session:
        reviewer = session.get(TrustedVerifier, reviewer_id)
        respond_to_practice_chart_verification(session, verifier=reviewer,
            verification_id=verification_id, decision='approved')
        with pytest.raises(ValueError):
            respond_to_practice_chart_verification(session, verifier=reviewer,
                verification_id=verification_id, decision='approved')
        assert xp_sources(session, profile_id=profile.id)['practice_minutes'] == 20
        assert xp_sources(session, profile_id=profile.id)['p_charts'] == 1
        assert session.scalar(select(func.count()).select_from(RewardGrant).where(RewardGrant.category_key=='practice')) == 1
    assert balance(authority_db, profile.id) == before + 4


UNCHECKED_GAMES = ('plunge-burrow','blue','radio-tuner','wheel-of-woodchuck',
    'scale-keyboard','thirds','dressed-to-the-nines','interval-basic-training')


@pytest.mark.parametrize('game', UNCHECKED_GAMES)
@pytest.mark.parametrize('score', [1, 2147483647])
def test_all_unchecked_arcade_aliases_withhold_value(authority_db, game, score):
    from app.models import ArcadeHighScore, ArcadePlaySession
    client, profile = signed_client(authority_db, 'ARCADE', credits=200)
    stranger, _ = signed_client(authority_db, 'OBSERVER')
    request_id = uuid4().hex
    start = client.post('/arcade/plays', json={'game_key':game, 'request_id':request_id})
    assert start.status_code == 200, start.text
    token = start.json()['play_token']
    assert client.post('/arcade/plays', json={'game_key':game, 'request_id':request_id}).json()['play_token'] == token
    before = balance(authority_db, profile.id)
    path = f'/arcade/plays/{token}/complete'
    assert stranger.post(path, json={'score':score}).status_code == 404
    for route, body in [(path, {'score':score}),
                        (f'/arcade/scores/{game}', {'play_token':token, 'score':score}),
                        (path, {'score':score})]:
        result = client.post(route, json=body)
        assert result.status_code == 200, result.text
        assert result.json()['payout'] == 0
        assert result.json()['result_authority'] == 'self_reported'
        assert result.json()['leaderboard'] == []
    assert client.post(path, json={'score':score-1}).status_code == 409
    assert balance(authority_db, profile.id) == before
    board = stranger.get('/xp/plunge-best' if game == 'plunge-burrow' else f'/arcade/scores/{game}')
    assert board.json()['leaderboard'] == []
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(ArcadeHighScore)) == 0
        play = session.scalar(select(ArcadePlaySession))
        assert play.payout == 0 and play.reward_granted_at is None


@pytest.mark.parametrize('score', [-1, 2147483648, 1.5, '1', True, None])
def test_arcade_malformed_scores_rejected(authority_db, score):
    client, _ = signed_client(authority_db, 'BAD-SCORE')
    token = client.post('/arcade/plays', json={'game_key':'blue', 'request_id':uuid4().hex}).json()['play_token']
    assert client.post(f'/arcade/plays/{token}/complete', json={'score':score}).status_code == 422


@pytest.mark.parametrize('game', ['unknown','rhythm-baseball','note-names'])
def test_unknown_disabled_games_cannot_start(authority_db, game):
    client, _ = signed_client(authority_db, 'DISABLED')
    assert client.post('/arcade/plays', json={'game_key':game, 'request_id':uuid4().hex}).status_code in (400,404)


@pytest.mark.parametrize('started', [False, True])
def test_standalone_plunge_xp_cannot_be_minted_even_with_a_play(authority_db, started):
    from app.models import PlungePointAward
    client, profile = signed_client(authority_db, 'PLUNGE-XP')
    if started:
        start = client.post('/arcade/plays', json={'game_key':'plunge-burrow', 'request_id':uuid4().hex})
        token = start.json()['play_token']
        assert client.post(f'/arcade/plays/{token}/complete', json={'score':100}).json()['payout'] == 0
    for key, event, points in [('same','band_complete',20), ('same','band_complete',20),
                               ('same','carrot',3), ('fresh','band_complete',20)]:
        response = client.post('/xp/plunge-points', json={
            'event_key':key, 'event_type':event, 'points_scored':points})
        assert response.status_code == 409
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(PlungePointAward)) == 0
        assert xp_sources(session, profile_id=profile.id)['plunge_points'] == 0


@pytest.mark.parametrize('points', [-1, 0, 99999999, 1.5, '20', True])
def test_plunge_arbitrary_amounts_cannot_persist(authority_db, points):
    client, _ = signed_client(authority_db, 'PLUNGE-BOUNDS')
    assert client.post('/xp/plunge-points', json={
        'event_key':uuid4().hex, 'event_type':'band_complete', 'points_scored':points}).status_code in (400,422)


@pytest.mark.parametrize('legacy', [False, True])
def test_bonus_aliases_reject_invented_completion_and_retries(authority_db, legacy):
    from app.models import QuestCompletion
    client, profile = signed_client(authority_db, 'BONUS')
    before = balance(authority_db, profile.id)
    challenge = client.get('/contests/bonus-challenge/current').json()['challenge']
    payload = {'activity_date':today()}
    if legacy:
        route = '/contests/quest/completions'
        payload.update(quest_id=challenge['challenge_id'], minutes=10, logged_minutes=1440)
    else:
        route = '/contests/bonus-challenge/progress'
        payload['challenge_instance'] = challenge['instance_key']
    for _ in range(3):
        response = client.post(route, json=payload)
        assert response.status_code == 409, response.text
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
        assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0
    assert balance(authority_db, profile.id) == before


def test_bonus_assignment_ignores_browser_daily_state(authority_db):
    client, _ = signed_client(authority_db, 'BONUS-STATE')
    before = client.get('/contests/bonus-challenge/current').json()
    payload = client.get('/account/state').json()
    state = payload['state']
    state.setdefault('account', {})['serverRevision'] = payload['revision']
    state['daily'] = {'dateKey':today(), 'questId':'invented', 'completed':True, 'loggedMinutes':99999}
    assert client.put('/account/state', json=state).status_code == 200
    assert client.get('/contests/bonus-challenge/current').json() == before


@pytest.mark.parametrize('identity', ['anonymous', 'guest', 'stale', 'inactive'])
@pytest.mark.parametrize('route,body', [
    ('/practice-charts', {'minutes':20,'submission_key':'auth','practice_date':today()}),
    ('/practice-charts/pristine', {'detected_playing_seconds':300,'submission_key':'auth'}),
    ('/arcade/plays', {'game_key':'blue','request_id':'auth-request-123456789'}),
    ('/xp/plunge-points', {'event_key':'auth','event_type':'band_complete','points_scored':20}),
    ('/contests/camp-points/awards', {'activity_type':'hours','activity_date':today()}),
    ('/contests/bonus-challenge/progress', {'activity_date':today(),'challenge_instance':'invented'}),
    ('/contests/quest/completions', {'activity_date':today(),'quest_id':'invented','minutes':10,'logged_minutes':10}),
])
def test_repaired_routes_preserve_authentication(authority_db, identity, route, body):
    from app.models import WoodchuckProfile
    if identity in ('anonymous','guest'):
        client = TestClient(main.app)
        if identity == 'guest':
            assert client.get('/guest').status_code == 200
    else:
        client, profile = signed_client(authority_db, 'AUTH')
        with authority_db() as session:
            saved = session.get(WoodchuckProfile, profile.id)
            if identity == 'stale':
                saved.session_version += 1
            else:
                saved.status = 'deleted'
            session.commit()
    response = client.post(route, json=body)
    assert response.status_code in (401,403), response.text


def test_private_family_practice_shares_containment_and_idempotency(authority_db):
    client, profile = signed_client(authority_db, 'FAMILY')
    before = balance(authority_db, profile.id)
    page = client.get('/family/practice')
    assert page.status_code == 200
    data = {'csrf':page.context['csrf'], 'submission_key':page.context['submission_key'],
            'practice_date':today(), 'minutes':'120', 'note':'Private synthetic log'}
    for _ in range(3):
        assert client.post('/family/practice', data=data, follow_redirects=False).status_code == 303
    assert client.post('/family/practice', data={**data, 'minutes':'121'}, follow_redirects=False).status_code == 409
    with authority_db() as session:
        chart = session.scalar(select(PracticeChart))
        assert chart.credits_awarded == 0 and not chart.include_contests and not chart.include_team_contests
        assert session.scalar(select(func.count()).select_from(PracticeChart)) == 1
        assert xp_sources(session, profile_id=profile.id)['practice_minutes'] == 0
    assert balance(authority_db, profile.id) == before


def test_unverified_legacy_rows_do_not_create_new_public_awards(authority_db):
    from app.models import CrownAward, ContestResult
    from tests.test_contests import NOW, FINAL_NOW
    client, profile = signed_client(authority_db, 'PROJECTIONS')
    with authority_db() as session:
        season, definitions, week = contests.ensure_band_camp_data(session, now=NOW)
        session.add_all([
            PracticeChart(profile_id=profile.id, practice_date=NOW.date(), minutes=1440,
                          source='p-book', instrument='Flute', include_contests=True),
            PracticeChart(profile_id=profile.id, practice_date=NOW.date(), minutes=1440,
                          detected_playing_seconds=86400, source='pristine', instrument='Flute', include_contests=True),
        ])
        for index in range(10):
            session.add(CampPointAward(profile_id=profile.id, activity_type='hours',
                points_awarded=1, occurred_at=NOW-timedelta(days=index), duplicate_key=f'legacy:{index}'))
        session.commit()
        assert contests._charts_and_approved_ids(session, week)[0] == []
        assert xp_sources(session, profile_id=profile.id)['board_points'] == 0
        contests._reconcile_crown_categories(session, profile_id=profile.id)
        contests.finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()
        assert session.scalar(select(func.count()).select_from(ContestResult)) == 0
        assert session.scalar(select(func.count()).select_from(CrownAward)) == 0


def test_review_reward_failure_rolls_back_approval_balance_and_grant(authority_db):
    from sqlalchemy import event
    from app.security import hash_pin
    client, profile = signed_client(authority_db, 'ROLLBACK')
    before = balance(authority_db, profile.id)
    with authority_db() as session:
        reviewer = TrustedVerifier(email='rollback@example.test', display_name='Reviewer', pin_hash=hash_pin('2468'))
        session.add(reviewer); session.flush()
        session.add(StudentVerifierConnection(profile_id=profile.id, verifier_id=reviewer.id, role='verifier', status='accepted'))
        chart = PracticeChart(profile_id=profile.id, practice_date=datetime.now(contests.CENTRAL).date(), minutes=20, instrument='Flute')
        session.add(chart); session.flush()
        verification = PracticeChartVerification(practice_chart_id=chart.id, verifier_id=reviewer.id, status='pending')
        session.add(verification); session.commit()
        vid, rid = verification.id, reviewer.id
    def fail(*args):
        raise RuntimeError('synthetic grant failure')
    event.listen(RewardGrant, 'before_insert', fail)
    try:
        with authority_db() as session:
            with pytest.raises(RuntimeError, match='synthetic grant failure'):
                respond_to_practice_chart_verification(session, verifier=session.get(TrustedVerifier,rid), verification_id=vid, decision='approved')
    finally:
        event.remove(RewardGrant, 'before_insert', fail)
    with authority_db() as session:
        assert session.get(PracticeChartVerification, vid).status == 'pending'
        assert session.scalar(select(func.count()).select_from(RewardGrant).where(RewardGrant.category_key=='practice')) == 0
    assert balance(authority_db, profile.id) == before


@pytest.mark.parametrize('seconds', [-1, 0, 86401, 10**15, 1.5, '60', True])
def test_pristine_invalid_duration_cannot_persist(authority_db, seconds):
    client, _ = signed_client(authority_db, 'PRISTINE-BOUNDS')
    assert client.post('/practice-charts/pristine', json={
        'detected_playing_seconds':seconds, 'submission_key':uuid4().hex}).status_code == 422


@pytest.mark.parametrize('request_id', ['', ' ', 'x'*65, 42, None])
def test_arcade_malformed_request_id_cannot_create_a_play(authority_db, request_id):
    from app.models import ArcadePlaySession
    client, _ = signed_client(authority_db, 'BAD-ID')
    result = client.post('/arcade/plays', json={'game_key':'blue','request_id':request_id})
    assert result.status_code in (400,404,422), result.text
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(ArcadePlaySession)) == 0


def test_bonus_cannot_assert_completion_or_identity_fields(authority_db):
    client, _ = signed_client(authority_db, 'EXTRA-FIELDS')
    challenge = client.get('/contests/bonus-challenge/current').json()['challenge']
    assert client.post('/contests/bonus-challenge/progress', json={
        'activity_date':today(),'challenge_instance':challenge['instance_key'],
        'completed':True, 'reward_amount':999, 'profile_id':999}).status_code == 422


def test_review_grants_share_server_earning_day_cap(authority_db):
    from app.security import hash_pin
    client, profile = signed_client(authority_db, 'REVIEW-CAP')
    before = balance(authority_db, profile.id)
    with authority_db() as session:
        reviewer = TrustedVerifier(email='cap@example.test', display_name='Reviewer', pin_hash=hash_pin('2468'))
        session.add(reviewer); session.flush()
        session.add(StudentVerifierConnection(profile_id=profile.id,verifier_id=reviewer.id,role='verifier',status='accepted'))
        session.commit(); rid = reviewer.id
    for offset in (0, 1):
        response = client.post('/practice-charts', json={**book(verifier_id=rid),
            'minutes':250, 'practice_date':(datetime.now(contests.CENTRAL).date()-timedelta(days=offset)).isoformat()})
        assert response.status_code == 201, response.text
        with authority_db() as session:
            respond_to_practice_chart_verification(session, verifier=session.get(TrustedVerifier,rid),
                verification_id=response.json()['chart']['verification']['id'], decision='approved')
    assert balance(authority_db, profile.id) == before + 75
    with authority_db() as session:
        assert sorted(session.scalars(select(PracticeChart.credits_awarded))) == [25,50]


from tests.test_server_economy import economy_db


def test_postgres_concurrent_practice_duplicate_is_one_log(economy_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models import WoodchuckProfile
    from app.practice_charts import create_practice_chart_verification_request
    if economy_db.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row locks require an explicit disposable database')
    barrier = Barrier(2)
    def save(_):
        with economy_db() as session:
            barrier.wait(timeout=5)
            result = create_practice_chart_verification_request(session,
                profile=session.get(WoodchuckProfile,1), verifier_id=None,
                practice_date=datetime.now(contests.CENTRAL).date(), minutes=20,
                submission_key='parallel-book')
            return result.created
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [False, True]
    with economy_db() as session:
        assert session.scalar(select(func.count()).select_from(PracticeChart)) == 1
        assert xp_sources(session, profile_id=1)['practice_minutes'] == 0


def test_postgres_concurrent_review_cannot_pay_twice(economy_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models import WoodchuckState
    from app.security import hash_pin
    if economy_db.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row locks require an explicit disposable database')
    with economy_db() as session:
        reviewer = TrustedVerifier(email='parallel@example.test', display_name='Reviewer', pin_hash=hash_pin('2468'))
        session.add(reviewer); session.flush()
        session.add(StudentVerifierConnection(profile_id=1,verifier_id=reviewer.id,role='verifier',status='accepted'))
        chart = PracticeChart(profile_id=1,practice_date=datetime.now(contests.CENTRAL).date(),minutes=20,instrument='Flute')
        session.add(chart); session.flush()
        verification = PracticeChartVerification(practice_chart_id=chart.id,verifier_id=reviewer.id,status='pending')
        session.add(verification); session.commit()
        rid, vid = reviewer.id, verification.id
        before = session.get(WoodchuckState,1).state_json['progress']['credits']
    barrier = Barrier(2)
    def approve(_):
        with economy_db() as session:
            barrier.wait(timeout=5)
            try:
                respond_to_practice_chart_verification(session,verifier=session.get(TrustedVerifier,rid),
                    verification_id=vid,decision='approved')
                return True
            except ValueError as error:
                assert 'already been answered' in str(error)
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(approve, range(2))) == [False,True]
    with economy_db() as session:
        assert session.get(WoodchuckState,1).state_json['progress']['credits'] == before+4
        assert session.scalar(select(func.count()).select_from(RewardGrant).where(RewardGrant.category_key=='practice')) == 1


from tests.test_director_dashboard import dashboard_database


@pytest.mark.parametrize('metric', ['total_minutes','average_minutes','team_practice_rating'])
def test_director_cannot_finalize_zero_score_from_unqualified_logs(dashboard_database, metric):
    from tests.test_director_dashboard import _event_setup
    from app.director_dashboard import finalize_director_contest
    from app.models import DirectorTeamContest, DirectorTeamContestResult, WoodchuckProfile
    cid, owner_id, _, _, end = _event_setup(dashboard_database, metric)
    with dashboard_database() as session:
        for review in session.scalars(select(PracticeChartVerification)):
            review.status = 'pending'
            review.responded_at = None
        session.commit()
        finalize_director_contest(session, contest=session.get(DirectorTeamContest,cid),
            profile=session.get(WoodchuckProfile,owner_id), now=end)
        assert session.scalar(select(func.count()).select_from(DirectorTeamContestResult)) == 0


def test_unknown_board_activity_is_not_a_qualified_ledger_source(authority_db):
    client, profile = signed_client(authority_db, 'UNKNOWN-BOARD')
    assert client.post('/contests/camp-points/awards', json={
        'activity_date':today(), 'activity_type':'invented'}).status_code == 400
    with authority_db() as session:
        session.add(CampPointAward(profile_id=profile.id, activity_type='invented',
            points_awarded=999, occurred_at=datetime.now(timezone.utc), duplicate_key='synthetic-legacy'))
        session.commit()
        assert xp_sources(session, profile_id=profile.id)['board_points'] == 0


def test_plunge_malformed_event_ids_never_create_ledger_entries(authority_db):
    from app.models import PlungePointAward
    client, _ = signed_client(authority_db, 'PLUNGE-ID')
    for key in ('', ' ', 'x' * 101, 42, None):
        result = client.post('/xp/plunge-points', json={
            'event_key':key, 'event_type':'band_complete', 'points_scored':20})
        assert result.status_code in (400, 422), result.text
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(PlungePointAward)) == 0


def test_pristine_retries_changed_payload_and_malformed_ids(authority_db):
    client, profile = signed_client(authority_db, 'PRISTINE-ID')
    payload = {'detected_playing_seconds':60, 'submission_key':'private-timer'}
    for index in range(3):
        result = client.post('/practice-charts/pristine', json=payload)
        assert result.status_code == 201, result.text
        assert result.json()['created'] is (index == 0)
    assert client.post('/practice-charts/pristine', json={
        **payload, 'detected_playing_seconds':61}).status_code == 400
    for key in ('', ' ', 'x' * 65, 42, None):
        assert client.post('/practice-charts/pristine', json={
            **payload, 'submission_key':key}).status_code in (400, 422)
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(PracticeChart)) == 1
        assert xp_sources(session, profile_id=profile.id)['practice_minutes'] == 0


@pytest.mark.parametrize('legacy', [False, True])
def test_bonus_invalid_assignments_dates_and_durations_never_complete(authority_db, legacy):
    from app.models import QuestCompletion
    client, profile = signed_client(authority_db, 'BONUS-INVALID')
    challenge = client.get('/contests/bonus-challenge/current').json()['challenge']
    route = '/contests/quest/completions' if legacy else '/contests/bonus-challenge/progress'
    field = 'quest_id' if legacy else 'challenge_instance'
    payload = {'activity_date':today(), field:challenge['challenge_id' if legacy else 'instance_key']}
    if legacy:
        payload.update(minutes=10, logged_minutes=10)
    before = balance(authority_db, profile.id)
    changes = [{field:value} for value in ('invented', '', 'x' * 201, None)]
    changes += [{'activity_date':value} for value in ('1900-01-01', 'malformed')]
    changes += [{'minutes':value} for value in (-1, 0, 10**15, 1.5, '10', True)]
    for change in changes:
        result = client.post(route, json={**payload, **change})
        assert result.status_code in (400, 409, 422), result.text
    with authority_db() as session:
        assert session.scalar(select(func.count()).select_from(QuestCompletion)) == 0
        assert session.scalar(select(func.count()).select_from(CampPointAward)) == 0
    assert balance(authority_db, profile.id) == before
