from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import analytics_routes, membership_routes, verifier_routes, store_routes, contests, arcade_routes
from app.models import (PracticeChart, StudentVerifierConnection, TrustedVerifier,
                        TrustedVerifierInvitation)
from app.security import hash_invitation_token, hash_pin
from tests.test_analytics import db, client, student_client, admin_client
from tests.test_arcade_economy import economy_database, signed_client as arcade_client


@pytest.fixture
def identities(db, monkeypatch):
    for module in (membership_routes, verifier_routes, store_routes, contests):
        monkeypatch.setattr(module, 'SessionLocal', db)
    with db() as session:
        pin_hash = hash_pin('2468')
        for number in (1,2):
            session.add(TrustedVerifier(id=number, email=f'adult-{number}@example.test',
                                       display_name=f'Verifier {number}', pin_hash=pin_hash))
        session.flush()
        for number in (1,2):
            session.add(StudentVerifierConnection(id=number, profile_id=number,
                verifier_id=number, role='verifier', status='accepted'))
            session.add(TrustedVerifierInvitation(id=number, profile_id=number,
                email=f'pending-{number}@example.test', role='parent',
                token_hash=hash_invitation_token(f'isolated-{number}'),
                expires_at=datetime.now(timezone.utc)+timedelta(days=1)))
        session.add(PracticeChart(profile_id=2, practice_date=date.today(), minutes=37,
            instrument='Flute', note='Student B private practice note', credits_awarded=0))
        session.commit()
    return db


@pytest.mark.parametrize('identity', [None, {'woodchuck_profile_id':1, 'woodchuck_session_version':0},
    {'trusted_verifier_id':1}, {'trusted_verifier_id':2}, {'site_admin_fingerprint':'forged'}])
@pytest.mark.parametrize('path', ['/admin/analytics', '/admin/security/rate-limit'])
def test_private_admin_reads_reject_forged_roles_before_data_access(identities, monkeypatch, identity, path):
    from app import login_limits
    def forbidden(*args, **kwargs):
        pytest.fail('Unauthorized request reached protected data/service')
    monkeypatch.setattr(analytics_routes, 'build_report', forbidden)
    monkeypatch.setattr(login_limits, 'protection_status', forbidden)
    monkeypatch.setattr(membership_routes, 'protection_status', forbidden)
    response = client(identity).get(path+'?profile_id=2&admin=true', headers={'X-Admin':'true'})
    assert response.status_code == 403
    assert 'Student B private' not in response.text


def test_real_admin_diagnostics_and_no_hidden_analytics_api(identities):
    admin = admin_client()
    response = admin.get('/admin/security/rate-limit')
    assert response.status_code == 200
    assert response.json()['state'] == 'disabled'
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['production_verified'] is False
    from app.main import app
    paths = [route.path for route in app.routes if 'analytics' in route.path]
    assert paths == ['/admin/analytics']


@pytest.mark.parametrize('method,path', [
    ('delete','/trusted-verifiers/invitations/2'),
    ('post','/trusted-verifiers/invitations/2/reissue'),
    ('post','/trusted-verifiers/invitations/2/resend-email'),
    ('delete','/trusted-verifiers/connections/2'),
])
def test_student_cannot_mutate_another_students_invitation_or_relationship(identities, method, path):
    response = getattr(student_client(1),method)(path)
    assert response.status_code == 404
    with identities() as session:
        assert session.get(TrustedVerifierInvitation,2).status == 'pending'
        assert session.get(StudentVerifierConnection,2).status == 'accepted'


def test_query_selectors_cannot_change_private_student_scope(identities):
    first, second = student_client(1), student_client(2)
    assert second.get('/practice-charts').json()['charts'][0]['note'] == 'Student B private practice note'
    for path in ['/practice-charts?profile_id=2&student_id=2',
                 '/account/state?profile_id=2', '/store/inventory?profile_id=2']:
        response = first.get(path)
        assert response.status_code == 200
        assert 'Student B private practice note' not in response.text
        assert 'WC-AN-2' not in response.text
    assert first.get('/practice-charts?profile_id=2').json()['charts'] == []


def test_open_arcade_score_forgery_consequences_are_characterized(economy_database):
    """OPEN finding: this asserts observed consequences, not successful remediation."""
    from app.arcade_scores import MAX_ARCADE_SCORE
    first, profile = arcade_client(economy_database, 'FORGER', credits=20)
    start = first.post('/arcade/plays', json={'game_key':'blue'})
    assert start.status_code == 200
    path = '/arcade/plays/'+start.json()['play_token']+'/complete'
    result = first.post(path, json={'score':MAX_ARCADE_SCORE})
    assert result.status_code == 200
    assert result.json()['payout'] == 5
    assert result.json()['balance'] == 24  # Forged score yields four net credits.
    assert first.post(path, json={'score':MAX_ARCADE_SCORE}).json()['balance'] == 24
    second, _ = arcade_client(economy_database, 'OTHER', credits=20)
    assert second.post(path, json={'score':MAX_ARCADE_SCORE}).status_code == 404
    board = second.get('/arcade/scores/blue')
    assert str(MAX_ARCADE_SCORE) in board.text  # Shared score integrity affected.
    assert profile.woodchuck_id not in board.text


def test_database_failures_do_not_log_sql_tokens_or_arbitrary_headers(economy_database, monkeypatch, caplog):
    # Earlier migration tests use fileConfig(), which disables existing loggers.
    # Exercise actual log output regardless of collection order; restore afterward.
    monkeypatch.setattr(arcade_routes.logger, 'disabled', False)
    caplog.set_level('ERROR', logger=arcade_routes.logger.name)
    first, _ = arcade_client(economy_database, 'LOGS', credits=20)
    def fail(*args, **kwargs):
        raise SQLAlchemyError('private-play-token secret-db-password private-session-cookie')
    monkeypatch.setattr(arcade_routes, 'start_arcade_play', fail)
    response = first.post('/arcade/plays', json={'game_key':'blue'},
                          headers={'X-Woodshed-Arcade-Game':'injected-private-pin'})
    assert response.status_code == 503
    assert 'arcade_request_failed operation=start game_key=unknown' in caplog.text
    for secret in ('private-play-token','secret-db-password','private-session-cookie','injected-private-pin'):
        assert secret not in caplog.text + response.text
