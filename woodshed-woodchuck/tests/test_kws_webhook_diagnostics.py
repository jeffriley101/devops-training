"""Privacy-safe diagnostics for synthetic production KWS callbacks."""
import hashlib
import hmac
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import child_authorization as consent, kws_client, kws_routes
from app import kws_verification as kws
from app.child_models import ConsentEvidence, PendingConsent
from app.kws_models import KWSVerification
from app.security import hash_invitation_token
from test_kws_production import production_db, make_profile, make_pending, choices


WEBHOOK = '/family/kws/production/webhook'
BODY_MARKER = 'private-body-marker'
QUERY_MARKER = 'private-query-marker'
COOKIE_MARKER = 'private-cookie-marker'
CREDENTIAL_MARKER = 'private-credential-marker'
TRANSACTION_MARKER = 'private-transaction-marker'


def started_verification(factory, monkeypatch):
    monkeypatch.setattr(kws_routes, 'SessionLocal', factory)
    monkeypatch.setattr(kws.client, 'send_email', lambda *_: True)
    with factory() as session:
        profile = make_profile(session)
        _, token = make_pending(session, profile)
        verification_id, payload = kws.start(session, token, choices())
        assert kws.deliver(session, verification_id, payload) == 'accepted'
    return verification_id, payload


def signed_header(raw, cfg, timestamp):
    signature = hmac.new(cfg.webhook_secrets[0].encode(),
                         str(timestamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={signature}'


def event_body(cfg, payload):
    return {'name': 'parent-verified', 'orgId': cfg.org_id, 'productId': cfg.product_id,
            'note': BODY_MARKER,
            'payload': {'parentEmail': 'parent@example.test', 'externalPayload': payload,
                        'status': {'verified': True, 'transactionId': TRANSACTION_MARKER}}}


def rejection_records(caplog):
    return [record for record in caplog.records if record.name == kws_routes.__name__]


@pytest.mark.parametrize('case,stage', [
    ('missing_header', 'malformed_signature_header'),
    ('duplicate_timestamp', 'malformed_signature_header'),
    ('malformed_v1', 'malformed_signature_header'),
    ('oversized_body', 'oversized_body'),
    ('stale_timestamp', 'invalid_timestamp'),
    ('future_timestamp', 'invalid_timestamp'),
    ('signature_mismatch', 'signature_mismatch'),
    ('invalid_json', 'invalid_json'),
    ('invalid_utf8', 'invalid_json'),
    ('wrong_event_name', 'wrong_event_name'),
    ('org_mismatch', 'org_mismatch'),
    ('product_mismatch', 'product_mismatch'),
    ('bad_parent_email_shape', 'invalid_payload_shape'),
    ('bad_status_shape', 'invalid_payload_shape'),
    ('unbound_payload', 'unbound_payload'),
    ('parent_email_mismatch', 'parent_email_mismatch'),
    ('binding_rejected', 'binding_rejected'),
    ('completion_rejected', 'completion_rejected'),
])
def test_rejected_webhook_logs_only_safe_stage(production_db, monkeypatch, caplog, case, stage):
    verification_id, payload = started_verification(production_db, monkeypatch)
    cfg = kws_client.Config.load('production')
    event = event_body(cfg, payload)
    timestamp = int(consent.clock().timestamp())
    if case == 'stale_timestamp':
        timestamp -= 86401
    elif case == 'future_timestamp':
        timestamp += 301
    elif case == 'completion_rejected':
        with production_db() as session:
            created_at = session.get(KWSVerification, verification_id).created_at
        timestamp = int(consent.utc(created_at).timestamp()) - 301
    elif case == 'wrong_event_name':
        event['name'] = 'private-wrong-event'
    elif case == 'org_mismatch':
        event['orgId'] = 'private-wrong-org'
    elif case == 'product_mismatch':
        event['productId'] = 'private-wrong-product'
    elif case == 'bad_parent_email_shape':
        event['payload']['parentEmail'] = ['private-invalid-email']
    elif case == 'bad_status_shape':
        event['payload']['status']['verified'] = 'true'
    elif case == 'unbound_payload':
        event['payload']['externalPayload'] = 'u' * 43
    elif case == 'parent_email_mismatch':
        event['payload']['parentEmail'] = 'private-wrong-parent@example.test'
    elif case == 'binding_rejected':
        with production_db() as session:
            verification = session.get(KWSVerification, verification_id)
            session.get(PendingConsent, verification.pending_id).director_name = 'Private changed name'
            session.commit()

    raw = json.dumps(event).encode()
    if case == 'invalid_json':
        raw = b'{"private-body-marker":'
    elif case == 'invalid_utf8':
        raw = b'{"private-body-marker":"\xff"}'
    elif case == 'oversized_body':
        raw = BODY_MARKER.encode() + b'x' * 65537
    header = signed_header(raw, cfg, timestamp)
    if case == 'duplicate_timestamp':
        header += f',t={timestamp}'
    elif case == 'malformed_v1':
        header = f't={timestamp},v1=private-invalid-signature'
    elif case == 'signature_mismatch':
        header = f't={timestamp},v1=' + '0' * 64

    headers = {'Cookie': f'private={COOKIE_MARKER}',
               'Authorization': f'Bearer {CREDENTIAL_MARKER}'}
    if case != 'missing_header':
        headers['x-kws-signature'] = header
    logger = logging.getLogger(kws_routes.__name__)
    monkeypatch.setattr(logger, 'disabled', False)
    caplog.set_level(logging.WARNING, logger=logger.name)
    caplog.clear()
    result = TestClient(app).post(WEBHOOK, content=raw, headers=headers,
                                  params={'private': QUERY_MARKER})

    assert result.status_code == 400
    assert result.json() == {'detail': 'Invalid KWS result.'}
    records = rejection_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].getMessage() == stage
    transaction_hash = hashlib.sha256(json.dumps(
        [cfg.environment, cfg.org_id, cfg.product_id, TRANSACTION_MARKER],
        separators=(',', ':')).encode()).hexdigest()
    for sensitive in (raw.decode(errors='replace'), BODY_MARKER, QUERY_MARKER, COOKIE_MARKER,
                      CREDENTIAL_MARKER, TRANSACTION_MARKER, payload, event['payload']['parentEmail'],
                      header, cfg.webhook_secrets[0], cfg.verification_secrets[0],
                      cfg.api_key, hashlib.sha256(raw).hexdigest(),
                      hash_invitation_token(payload), transaction_hash):
        if isinstance(sensitive, str):
            assert sensitive not in caplog.text
    with production_db() as session:
        verification = session.get(KWSVerification, verification_id)
        assert verification.state == 'accepted'
        assert verification.completed_at is None
        assert verification.activated_consent_id is None
        assert session.query(ConsentEvidence).count() == 0


def test_valid_webhook_preserves_success_without_rejection_log(production_db, monkeypatch, caplog):
    verification_id, payload = started_verification(production_db, monkeypatch)
    cfg = kws_client.Config.load('production')
    raw = json.dumps(event_body(cfg, payload)).encode()
    header = signed_header(raw, cfg, int(consent.clock().timestamp()))
    logger = logging.getLogger(kws_routes.__name__)
    monkeypatch.setattr(logger, 'disabled', False)
    caplog.set_level(logging.WARNING, logger=logger.name)
    caplog.clear()

    result = TestClient(app).post(WEBHOOK, content=raw,
                                  headers={'x-kws-signature': header})

    assert result.status_code == 200
    assert result.json() == {'received': True}
    assert rejection_records(caplog) == []
    with production_db() as session:
        verification = session.get(KWSVerification, verification_id)
        assert verification.state == 'verified'
        assert verification.completed_at is not None
        assert verification.activated_consent_id is None


def test_valid_browser_response_preserves_shared_validation(production_db, monkeypatch):
    verification_id, payload = started_verification(production_db, monkeypatch)
    cfg = kws_client.Config.load('production')
    status = json.dumps({'verified': True, 'transactionId': TRANSACTION_MARKER,
                         'timestamp': int(consent.clock().timestamp())})
    signature = hmac.new(cfg.verification_secrets[0].encode(),
                         (status + ':' + payload).encode(), hashlib.sha256).hexdigest()

    result = TestClient(app).get('/family/kws/production/response',
                                 params={'status': status, 'externalPayload': payload,
                                         'signature': signature})

    assert result.status_code == 200
    with production_db() as session:
        verification = session.get(KWSVerification, verification_id)
        assert verification.state == 'verified'
        assert verification.completed_at is not None


def test_invalid_browser_response_stays_generic(production_db, monkeypatch):
    verification_id, payload = started_verification(production_db, monkeypatch)
    status = json.dumps({'verified': True, 'transactionId': TRANSACTION_MARKER,
                         'timestamp': int(consent.clock().timestamp())})

    result = TestClient(app).get('/family/kws/production/response',
                                 params={'status': status, 'externalPayload': payload,
                                         'signature': '0' * 64})

    assert result.status_code == 400
    assert result.json() == {'detail': 'Invalid KWS result.'}
    with production_db() as session:
        verification = session.get(KWSVerification, verification_id)
        assert verification.state == 'accepted'
        assert verification.completed_at is None


def test_hmac_checks_every_secret_and_candidate(monkeypatch):
    message = b'synthetic-message'
    keys = ['synthetic-first-secret', 'synthetic-second-secret']
    first = hmac.new(keys[0].encode(), message, hashlib.sha256).hexdigest()
    second = hmac.new(keys[1].encode(), message, hashlib.sha256).hexdigest()
    actual_compare = hmac.compare_digest
    comparisons = []

    def record_compare(expected, supplied):
        comparisons.append((expected, supplied))
        return actual_compare(expected, supplied)

    monkeypatch.setattr(kws.hmac, 'compare_digest', record_compare)
    kws.signatures_match(message, [first, '0' * 64, second], keys)
    assert len(comparisons) == 6
