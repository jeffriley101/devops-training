"""Student and parent status copy from durable, read-only consent state."""
from datetime import datetime, timedelta, timezone
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import Base
from app.age_privacy import declare_age
from app import child_authorization as consent
from app.child_authorization import NOTICE_VERSION
from app.child_models import PendingConsent
from app.consent_status import student_consent_status
from app.kws_models import KWSVerification
from app.models import WoodchuckProfile
from app.security import hash_invitation_token, hash_pin
from test_age_screening import age_db
from test_private_practice import captured


@pytest.fixture
def status_db(tmp_path, monkeypatch, captured):
    engine = create_engine(f"sqlite:///{tmp_path / 'status.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    with factory() as session:
        profile = WoodchuckProfile(woodchuck_id='WC-STATUS', display_name='Synthetic Student',
            pin_hash=hash_pin('2468'), instrument='Flute', level='Beginner', goal='Practice')
        session.add(profile)
        session.flush()
        declare_age(session, profile.id, 'under13')
        session.commit()
    yield factory
    engine.dispose()


def login():
    client = TestClient(app)
    result = client.post('/account/login', data={'woodchuck_id': 'WC-STATUS', 'pin': '2468'})
    assert result.status_code == 200, result.text
    return client


def flow(factory, state=None, *, expired=False, created=None, notice_version=NOTICE_VERSION):
    instant = created or datetime.now(timezone.utc)
    with factory() as session:
        pending = PendingConsent(profile_id=1, parent_email='secret-parent@example.test',
            director_email='', director_name='', review_allowed=False,
            approve_hash=hash_invitation_token('synthetic-approval-token-123456789'),
            created_at=instant, expires_at=instant + (timedelta(hours=-1) if expired else timedelta(hours=24)),
            notice_version=notice_version)
        session.add(pending)
        session.flush()
        if state:
            session.add(KWSVerification(pending_id=pending.id, payload_hash='s' * 64,
                environment='test', org_id='private-provider-id', product_id=None,
                binding_sha256='b' * 64, notice_sha256='n' * 64,
                guardian_attested_at=instant, notice_accepted_at=instant,
                account_allowed=True, director_allowed=False,
                created_at=instant, expires_at=pending.expires_at, state=state))
        session.commit()
    return '/family/approve/synthetic-approval-token-123456789'


def test_no_flow_shows_start_action(status_db):
    client = login()
    page = client.get('/account/age')
    assert 'Parent permission has not been started yet' in page.text
    assert '<h1>Parent permission</h1>' in page.text
    assert 'Start a parent permission request' in page.text
    assert 'href="/family/request"' in page.text
    assert 'name="parent_email"' in client.get('/family/request').text


@pytest.mark.parametrize('state,expected', [
    (None, 'A parent permission request has already been sent'),
    ('reserved', 'Parent verification is being prepared or is in progress'),
    ('accepted', 'Parent verification is being prepared or is in progress'),
    ('delivery_unknown', 'Woodshed could not confirm KWS delivery or completion status'),
    ('verified', 'permission is not active yet'),
    ('failed', 'Parent verification did not complete'),
    ('activated', 'Parent permission is not currently available'),
    ('unexpected', 'Woodshed could not confirm the current permission status'),
])
def test_active_flow_status_suppresses_duplicate_form_and_private_data(status_db, state, expected):
    flow(status_db, state)
    client = login()
    for path in ('/account/age', '/family/request'):
        page = client.get(path)
        assert page.status_code == 200
        assert expected in page.text
        assert 'name="parent_email"' not in page.text
        for secret in ('secret-parent@example.test', 'private-provider-id', 's' * 64):
            assert secret not in page.text
    if state == 'delivery_unknown':
        assert 'do not repeatedly restart or resend' in client.get('/account/age').text
        assert 'href="/account/help"' in client.get('/family/request').text
    if state in ('reserved', 'accepted'):
        assert 'If no message arrived' in client.get('/account/age').text
        assert 'href="/account/help"' in client.get('/family/request').text
    request_page = client.get('/family/request')
    result = client.post('/family/request', data={'csrf': request_page.context['csrf'],
        'confirm_account': 'WC-STATUS', 'parent_email': 'another-parent@example.test'})
    assert result.status_code == 409
    with status_db() as session:
        assert session.scalar(select(func.count(PendingConsent.id))) == 1


def test_expired_flow_offers_fresh_request(status_db):
    flow(status_db, expired=True)
    client = login()
    assert 'previous parent permission request expired' in client.get('/account/age').text
    page = client.get('/family/request')
    assert 'Start a new request below' in page.text
    assert 'name="parent_email"' in page.text
    response = client.post('/family/request', data={'csrf': page.context['csrf'],
        'confirm_account': 'WC-STATUS', 'parent_email': 'fresh-parent@example.test'})
    assert response.status_code == 200, response.text
    with status_db() as session:
        assert session.scalar(select(func.count(PendingConsent.id))) == 2


def test_superseded_unexpired_request_allows_fresh_request_without_changing_old_row(status_db):
    flow(status_db, notice_version='older-notice-version')
    with status_db() as session:
        old = session.scalar(select(PendingConsent))
        old_id = old.id
        original = (old.parent_email, old.approve_hash, old.created_at, old.expires_at,
                    old.notice_version, old.confirmed_at)
    client = login()
    age_page = client.get('/account/age')
    assert 'previous parent permission request can no longer be used' in age_page.text
    assert 'Start a new parent permission request' in age_page.text
    request_page = client.get('/family/request')
    assert 'name="parent_email"' in request_page.text
    assert 'Start a new request below' in request_page.text
    response = client.post('/family/request', data={'csrf': request_page.context['csrf'],
        'confirm_account': 'WC-STATUS', 'parent_email': 'secret-parent@example.test'})
    assert response.status_code == 200, response.text
    with status_db() as session:
        old = session.get(PendingConsent, old_id)
        assert (old.parent_email, old.approve_hash, old.created_at, old.expires_at,
                old.notice_version, old.confirmed_at) == original
        rows = list(session.scalars(select(PendingConsent).order_by(PendingConsent.id)))
        assert len(rows) == 2
        assert rows[1].notice_version == consent.notice_policy()[0]


def test_superseded_request_recovery_on_postgresql(age_db, captured):
    now = consent.clock()
    with age_db() as session:
        declare_age(session, 1, 'under13')
        old = PendingConsent(profile_id=1, parent_email='secret-parent@example.test',
            director_email='', director_name='', review_allowed=False,
            approve_hash=hash_invitation_token('postgres-old-approval-token-123456'),
            created_at=now, expires_at=now + timedelta(hours=24),
            notice_version='older-notice-version')
        session.add(old)
        session.commit()
        old_id = old.id
    client = TestClient(app)
    response = client.post('/account/login', data={'woodchuck_id': 'WC-AGE-A', 'pin': '2468'})
    assert response.status_code == 200, response.text
    assert 'can no longer be used' in client.get('/account/age').text
    page = client.get('/family/request')
    assert 'name="parent_email"' in page.text
    response = client.post('/family/request', data={'csrf': page.context['csrf'],
        'confirm_account': 'WC-AGE-A', 'parent_email': 'secret-parent@example.test'})
    assert response.status_code == 200, response.text
    with age_db() as session:
        rows = list(session.scalars(select(PendingConsent).order_by(PendingConsent.id)))
        assert len(rows) == 2
        assert rows[0].id == old_id
        assert rows[0].notice_version == 'older-notice-version'
        assert rows[0].expires_at == old.expires_at
        assert rows[1].notice_version == consent.notice_policy()[0]


def test_unknown_notice_policy_blocks_new_request_safely(status_db, monkeypatch):
    flow(status_db, notice_version='older-notice-version')
    def unavailable():
        raise ValueError('Synthetic policy unavailable')
    monkeypatch.setattr(consent, 'notice_policy', unavailable)
    client = login()
    assert 'could not confirm the current permission status' in client.get('/account/age').text
    request_page = client.get('/family/request')
    assert 'name="parent_email"' not in request_page.text
    response = client.post('/family/request', data={'csrf': request_page.context['csrf'],
        'confirm_account': 'WC-STATUS', 'parent_email': 'secret-parent@example.test'})
    assert response.status_code == 503
    assert 'status is temporarily unavailable' in response.text
    with status_db() as session:
        assert session.scalar(select(func.count(PendingConsent.id))) == 1


@pytest.mark.parametrize('state,expected,form_visible', [
    ('activated', 'Parent permission is not currently available', False),
    ('cancelled', 'previous parent permission request ended', True),
])
def test_closed_and_activated_requests_are_not_called_expired(status_db, state, expected, form_visible):
    flow(status_db, state, expired=True)
    client = login()
    assert expected in client.get('/account/age').text
    assert ('name="parent_email"' in client.get('/family/request').text) == form_visible


@pytest.mark.parametrize('state,expected', [
    ('delivery_unknown', 'KWS delivery or completion status could not be confirmed'),
    ('accepted', 'Adult verification in progress'),
    ('reserved', 'Adult verification in progress'),
    ('verified', 'Adult verification completed'),
])
def test_parent_approval_status(status_db, state, expected):
    url = flow(status_db, state)
    page = TestClient(app).get(url)
    assert page.status_code == 200
    assert expected in page.text
    if state == 'delivery_unknown':
        assert '<h3>Adult verification in progress</h3>' not in page.text
    if state in ('reserved', 'accepted'):
        assert 'If you receive a KWS adult-verification message' in page.text
        assert 'If no message arrived' in page.text


def test_status_helper_does_not_write(status_db):
    flow(status_db, 'accepted')
    writes = []
    engine = status_db.kw['bind']
    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            writes.append(statement)
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with status_db() as session:
            assert student_consent_status(session, 1) == 'kws_pending'
            assert not session.new and not session.dirty and not session.deleted
            session.commit()
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert writes == []
