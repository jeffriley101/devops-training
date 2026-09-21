"""Production KWS configuration isolation and fail-closed authorization tests."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import Base
from app import age_privacy, child_authorization as consent, kws_client
from app import kws_verification as kws
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence, PendingConsent
from app.kws_models import KWSVerification
from app.models import WoodchuckProfile
from app.security import generate_invitation_token, hash_invitation_token, hash_pin


PRODUCTION_ENV = {
    'APP_ENV': 'production',
    'SESSION_SECRET': 'synthetic-production-session-secret-12345',
    'KWS_PRODUCTION_ENABLED': 'true',
    'KWS_PRODUCTION_ENVIRONMENT': 'production',
    'KWS_PRODUCTION_CLIENT_ID': 'synthetic-production-client',
    'KWS_PRODUCTION_API_KEY': 'synthetic-production-key',
    'KWS_PRODUCTION_ORG_ID': 'synthetic-production-org',
    'KWS_PRODUCTION_LOCATION_JSON': '"US"',
    'KWS_PRODUCTION_WEBHOOK_SECRETS': '["synthetic-production-webhook"]',
    'KWS_PRODUCTION_VERIFICATION_SECRETS': '["synthetic-production-response"]',
}

TEST_ENV = {
    'APP_ENV': 'kws-test',
    'KWS_TEST_ENABLED': 'true',
    'KWS_ENVIRONMENT': 'test',
    'KWS_TEST_DATABASE_CONFIRMED': 'true',
    'KWS_TEST_CLIENT_ID': 'synthetic-test-client',
    'KWS_TEST_API_KEY': 'synthetic-test-key',
    'KWS_TEST_ORG_ID': 'synthetic-test-org',
    'KWS_TEST_LOCATION_JSON': '"US"',
    'KWS_TEST_WEBHOOK_SECRETS': '["synthetic-test-webhook"]',
    'KWS_TEST_VERIFICATION_SECRETS': '["synthetic-test-response"]',
    'KWS_TEST_RECIPIENTS_JSON': '["parent@example.test"]',
}


def configure(monkeypatch, values):
    for name in set(PRODUCTION_ENV) | set(TEST_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def production_db(monkeypatch):
    configure(monkeypatch, PRODUCTION_ENV)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    engine.dispose()


def make_profile(session, suffix='A'):
    profile = WoodchuckProfile(woodchuck_id=f'WC-PROD-{suffix}', display_name='Synthetic Child',
        pin_hash=hash_pin('2468'), instrument='Flute', level='Beginner',
        goal='Practice every day')
    session.add(profile)
    session.flush()
    return profile


def make_pending(session, profile):
    token = generate_invitation_token()
    now = datetime.now(timezone.utc)
    row = PendingConsent(profile_id=profile.id, parent_email='parent@example.test',
        director_email='', director_name='', review_allowed=False,
        approve_hash=hash_invitation_token(token), created_at=now,
        expires_at=now + timedelta(hours=48), notice_version=consent.PRODUCTION_NOTICE_VERSION)
    session.add(row)
    session.commit()
    return row, token


def choices():
    return {'account_allowed': 'yes', 'guardian_attestation': 'yes',
            'notice_accepted': 'yes', 'notice_version': consent.PRODUCTION_NOTICE_VERSION}


def signed_production_webhook(cfg, payload, *, verified=True, transaction='production-transaction'):
    timestamp = str(int(consent.clock().timestamp()))
    event = {'name': 'parent-verified', 'orgId': cfg.org_id, 'productId': cfg.product_id,
             'payload': {'parentEmail': 'parent@example.test', 'externalPayload': payload,
                         'status': {'verified': verified, 'transactionId': transaction}}}
    raw = json.dumps(event).encode()
    signature = hmac.new(cfg.webhook_secrets[0].encode(), timestamp.encode() + b'.' + raw,
                         hashlib.sha256).hexdigest()
    return raw, f't={timestamp},v1={signature}'


def test_production_is_disabled_by_default_and_incomplete_configuration_fails_closed(monkeypatch):
    configure(monkeypatch, {'APP_ENV': 'production',
                            'SESSION_SECRET': PRODUCTION_ENV['SESSION_SECRET']})
    assert not kws_client.production_enabled()
    assert not consent.under13_available()
    with pytest.raises(kws_client.KWSUnavailable):
        kws_client.Config.load('production')
    assert TestClient(app).post('/family/kws/production/webhook').status_code == 503

    monkeypatch.setenv('KWS_PRODUCTION_ENABLED', 'true')
    monkeypatch.setenv('KWS_PRODUCTION_ENVIRONMENT', 'production')
    assert not consent.under13_available()
    with pytest.raises(kws_client.KWSUnavailable):
        kws_client.Config.load('production')


def test_test_and_production_credentials_are_strictly_isolated(monkeypatch):
    configure(monkeypatch, {**PRODUCTION_ENV, **{k: v for k, v in TEST_ENV.items() if k != 'APP_ENV'}})
    production = kws_client.Config.load('production')
    assert production.environment == 'production'
    assert production.client_id == 'synthetic-production-client'
    with pytest.raises(kws_client.KWSUnavailable):
        kws_client.Config.load('test')
    assert TestClient(app).get('/family/kws/test/response').status_code == 503

    configure(monkeypatch, {**TEST_ENV, **{k: v for k, v in PRODUCTION_ENV.items() if k != 'APP_ENV'}})
    test = kws_client.Config.load('test')
    assert test.environment == 'test'
    assert test.client_id == 'synthetic-test-client'
    with pytest.raises(kws_client.KWSUnavailable):
        kws_client.Config.load('production')
    assert TestClient(app).get('/family/kws/production/response').status_code == 503


@pytest.mark.parametrize('runtime,wrong_secret', [
    ('production', 'synthetic-test-webhook'),
    ('test', 'synthetic-production-webhook'),
])
def test_callback_secrets_cannot_cross_environments(monkeypatch, runtime, wrong_secret):
    values = ({**PRODUCTION_ENV, **{k: v for k, v in TEST_ENV.items() if k != 'APP_ENV'}}
              if runtime == 'production'
              else {**TEST_ENV, **{k: v for k, v in PRODUCTION_ENV.items() if k != 'APP_ENV'}})
    configure(monkeypatch, values)
    cfg = kws_client.Config.load(runtime)
    timestamp = str(int(consent.clock().timestamp()))
    event = {'name': 'parent-verified', 'orgId': cfg.org_id, 'productId': cfg.product_id,
             'payload': {'parentEmail': 'parent@example.test', 'externalPayload': 'x' * 43,
                         'status': {'verified': True, 'transactionId': 'cross-environment'}}}
    raw = json.dumps(event).encode()
    signature = hmac.new(wrong_secret.encode(), timestamp.encode() + b'.' + raw,
                         hashlib.sha256).hexdigest()
    with pytest.raises(kws.InvalidResult):
        kws.webhook_result(raw, f't={timestamp},v1={signature}', cfg)


def test_valid_mocked_production_result_uses_secure_activation_path(production_db, monkeypatch):
    from app import kws_routes
    monkeypatch.setattr(kws_routes, 'SessionLocal', production_db)
    sent = []
    monkeypatch.setattr(kws.client, 'send_email', lambda cfg, email, payload:
                        sent.append((cfg.environment, email, payload)) or True)
    with production_db() as session:
        profile = make_profile(session)
        row, token = make_pending(session, profile)
        verification_id, payload = kws.start(session, token, choices())
        assert kws.deliver(session, verification_id, payload) == 'accepted'
        cfg = kws_client.Config.load('production')
        raw, signature = signed_production_webhook(cfg, payload)
        response = TestClient(app).post('/family/kws/production/webhook', content=raw,
                                        headers={'x-kws-signature': signature})
        assert response.status_code == 200
        session.expire_all()
        activation_token = consent.derived_token(row, 'activate')
        assert sent == [('production', 'parent@example.test', payload)]
        verification = session.get(KWSVerification, verification_id)
        assert verification.environment == 'production' and verification.state == 'verified'
        configure(monkeypatch, TEST_ENV)
        with pytest.raises(ValueError):
            consent.activate(session, activation_token, profile=profile, fields={})
        session.rollback()
        configure(monkeypatch, PRODUCTION_ENV)
        profile, evidence, permission = consent.activate(session, activation_token,
                                                          profile=profile, fields={})
        session.commit()
        assert permission is None
        assert evidence.notice_version == consent.PRODUCTION_NOTICE_VERSION
        assert evidence.notice_sha256 == consent.PRODUCTION_NOTICE_SHA256
        assert age_privacy.eligible(session, profile.id)


def test_invalid_production_result_cannot_authorize_child_data(production_db):
    with production_db() as session:
        profile = make_profile(session)
        row, token = make_pending(session, profile)
        verification_id, payload = kws.start(session, token, choices())
        cfg = kws_client.Config.load('production')
        timestamp = str(int(consent.clock().timestamp()))
        raw = json.dumps({'name': 'parent-verified', 'orgId': cfg.org_id,
            'payload': {'parentEmail': row.parent_email, 'externalPayload': payload,
                        'status': {'verified': True, 'transactionId': 'invalid'}}}).encode()
        with pytest.raises(kws.InvalidResult):
            kws.webhook_result(raw, f't={timestamp},v1=' + '0' * 64, cfg)
        session.rollback()
        assert session.get(KWSVerification, verification_id).state == 'reserved'
        assert session.scalar(select(ConsentEvidence)) is None
        assert not age_privacy.eligible(session, profile.id)


def test_age_policy_keeps_unknown_and_under13_closed_without_blocking_adult(production_db,
                                                                            monkeypatch):
    with production_db() as session:
        unknown = make_profile(session, 'UNKNOWN')
        child = make_profile(session, 'CHILD')
        adult = make_profile(session, 'ADULT')
        age_privacy.declare_age(session, unknown.id, 'unknown')
        age_privacy.declare_age(session, child.id, 'under13')
        age_privacy.declare_age(session, adult.id, 'adult')
        session.commit()
        monkeypatch.setenv('KWS_PRODUCTION_ENABLED', 'false')
        assert not age_privacy.eligible(session, unknown.id)
        assert not age_privacy.eligible(session, child.id)
        assert age_privacy.eligible(session, adult.id)
        with pytest.raises(ValueError):
            consent.require_under13_review()


def test_old_email_only_approval_remains_unreachable(production_db):
    with production_db() as session:
        with pytest.raises(ValueError, match='email-only approval is disabled'):
            consent.approve(session, 'x' * 43, explicit='yes',
                            notice_version=consent.PRODUCTION_NOTICE_VERSION,
                            review_allowed='yes')
