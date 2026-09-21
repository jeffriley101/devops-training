"""Synthetic KWS only: signed delivery, durable races, consent scope and Free journey."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import hmac
import json
import threading
from urllib.parse import parse_qs
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app import kws_verification as kws, kws_client, child_authorization as consent
from app.kws_models import KWSVerification, KWSEmailBudget
from app.child_models import PendingConsent, ConsentEvidence, DirectorPermission
from app.models import WoodchuckProfile, Membership, PracticeChart
from app.security import hash_invitation_token
from test_age_screening import age_db, login, state, counts
from test_private_practice import captured, authorize, post, path, save, connect, open_parent

WEBHOOK = '/family/kws/test/webhook'
RESPONSE = '/family/kws/test/response'
WEB_SECRET = 'synthetic-webhook-secret'
RED_SECRET = 'synthetic-redirect-secret'


def choices(**extra):
    return {'account_allowed': 'yes', 'guardian_attestation': 'yes', 'notice_accepted': 'yes',
            'notice_version': consent.NOTICE_VERSION, **extra}


def prepare(captured, *, existing=True, tag='a', fields=None):
    mail, now = captured
    child, parent = TestClient(app), TestClient(app)
    if existing:
        login(child)
    result = post(child, '/family/request', {'parent_email': f'parent-{tag}@example.test',
                  'confirm_account': 'WC-AGE-A' if existing else 'new'})
    assert result.status_code == 200, result.text
    url = path(mail, 'approve')
    result = post(parent, url, choices() if fields is None else fields)
    assert result.status_code == 200, result.text
    payload = kws.captured_requests[-1][1] if kws.captured_requests else None
    return child, parent, url, payload


def event(payload, *, transaction='synthetic-transaction', verified=True, email='parent-a@example.test', **extra):
    return {'name': 'parent-verified', 'orgId': 'synthetic-org', 'productId': None,
            'payload': {'parentEmail': email, 'externalPayload': payload,
                        'status': {'verified': verified, 'transactionId': transaction}}, **extra}


def webhook(parent, captured, data, *, timestamp=None, secret=WEB_SECRET, raw=None, header=None):
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode() if raw is None else raw
    timestamp = str(int(captured[1][0].timestamp())) if timestamp is None else str(timestamp)
    signature = hmac.new(secret.encode(), timestamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return parent.post(WEBHOOK, content=raw, headers={'x-kws-signature': header or f't={timestamp},v1={signature}'})


def response(parent, captured, payload, *, transaction='synthetic-transaction', verified=True,
             timestamp=None, secret=RED_SECRET, status=None, extra=()):
    if status is None:
        status = json.dumps({'verified': verified, 'transactionId': transaction, 'errorCode': None,
                             'timestamp': int(captured[1][0].timestamp()) if timestamp is None else timestamp},
                            ensure_ascii=False, indent=2)
    signature = hmac.new(secret.encode(), (status + ':' + payload).encode(), hashlib.sha256).hexdigest()
    return parent.get(RESPONSE, params=[('status', status), ('externalPayload', payload),
                                       ('signature', signature), *extra])


def verification(factory):
    with factory() as s:
        return s.scalar(select(KWSVerification))


@pytest.mark.parametrize('first', ['webhook', 'response'])
def test_both_orders_complete_once_without_parent_login(age_db, captured, first):
    child, parent, url, payload = prepare(captured)
    before = state(age_db)
    a = lambda: webhook(parent, captured, event(payload))
    b = lambda: response(parent, captured, payload)
    for deliver in ([a, b, a, b] if first == 'webhook' else [b, a, b, a]):
        result = deliver()
        assert result.status_code == 200, result.text
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['referrer-policy'] == 'no-referrer'
    assert verification(age_db).state == 'verified'
    assert parent.get('/family/parent/data').status_code == 403
    assert child.get('/account/state').status_code == 403  # Separate activation still required.
    assert state(age_db) == before
    with age_db() as s:
        assert s.scalar(select(func.count(ConsentEvidence.id))) == 0
        assert s.scalar(select(func.count(Membership.id))) == 0
        assert len(payload) <= 250 and payload not in s.scalar(select(KWSVerification.payload_hash))


@pytest.mark.parametrize('attack', ['bad-signature', 'tamper', 'stale', 'future', 'duplicate-t',
                                    'wrong-org', 'wrong-product', 'wrong-email', 'unicode-email', 'unbound'])
def test_webhook_rejects_invalid_results(age_db, captured, attack):
    child, parent, url, payload = prepare(captured)
    data = event(payload)
    now = int(captured[1][0].timestamp())
    options = {}
    if attack == 'bad-signature': options['secret'] = 'wrong-secret'
    if attack == 'tamper':
        raw = json.dumps(data).encode()
        sig = hmac.new(WEB_SECRET.encode(), str(now).encode() + b'.' + raw, hashlib.sha256).hexdigest()
        options.update(raw=raw + b' ', header=f't={now},v1={sig}')
    if attack == 'stale': options['timestamp'] = now - 86401
    if attack == 'future': options['timestamp'] = now + 301
    if attack == 'duplicate-t': options['header'] = f't={now},t={now},v1=' + '0' * 64
    if attack == 'wrong-org': data['orgId'] = 'other-org'
    if attack == 'wrong-product': data['productId'] = 'other-product'
    if attack == 'wrong-email': data['payload']['parentEmail'] = 'other@example.test'
    if attack == 'unicode-email': data['payload']['parentEmail'] = 'autre-é@example.test'
    if attack == 'unbound': data['payload']['externalPayload'] = 'x' * 43
    assert webhook(parent, captured, data, **options).status_code == 400
    assert verification(age_db).state == 'accepted'
    assert child.get('/account/state').status_code == 403


@pytest.mark.parametrize('attack', ['bad-signature', 'stale', 'future', 'duplicate-status',
                                    'duplicate-payload', 'tamper', 'unsigned', 'non-boolean', 'duplicate-json'])
def test_redirect_rejects_invalid_results(age_db, captured, attack):
    child, parent, url, payload = prepare(captured)
    now = int(captured[1][0].timestamp())
    options = {}
    if attack == 'bad-signature': options['secret'] = 'wrong'
    if attack == 'stale': options['timestamp'] = now - 86401
    if attack == 'future': options['timestamp'] = now + 301
    if attack == 'duplicate-status': options['extra'] = [('status', '{}')]
    if attack == 'duplicate-payload': options['extra'] = [('externalPayload', payload)]
    if attack == 'tamper': options['extra'] = [('signature', 'wrong')]; options['secret'] = 'wrong'
    if attack == 'non-boolean': options['status'] = json.dumps({'verified': 'true', 'transactionId': 'a', 'timestamp': now})
    if attack == 'duplicate-json': options['status'] = f'{{"verified":false,"verified":true,"transactionId":"a","timestamp":{now}}}'
    result = (parent.get(RESPONSE, params={'status': '{}', 'externalPayload': payload}) if attack == 'unsigned'
              else response(parent, captured, payload, **options))
    assert result.status_code == 400
    assert verification(age_db).state == 'accepted'


def test_exact_utf8_and_rotated_secrets_and_signatures(age_db, captured, monkeypatch):
    child, parent, url, payload = prepare(captured)
    monkeypatch.setenv('KWS_TEST_WEBHOOK_SECRETS', json.dumps(['retired-web-key', WEB_SECRET]))
    monkeypatch.setenv('KWS_TEST_VERIFICATION_SECRETS', json.dumps(['retired-return-key', RED_SECRET]))
    now = str(int(captured[1][0].timestamp()))
    data = event(payload, transaction='transaction: café', note='raw UTF-8: é')
    raw = json.dumps(data, ensure_ascii=False, indent=3).encode()
    sig = hmac.new(WEB_SECRET.encode(), now.encode() + b'.' + raw, hashlib.sha256).hexdigest()
    assert webhook(parent, captured, data, raw=raw, header=f't={now},v1='+'0'*64+f',v1={sig}').status_code == 200
    assert response(parent, captured, payload, transaction='transaction: café', extra=[('signature', '0'*64)]).status_code == 200


@pytest.mark.parametrize('binding_change', ['parent', 'child', 'director', 'notice', 'session', 'environment', 'scope'])
def test_signed_result_cannot_change_request_binding(age_db, captured, monkeypatch, binding_change):
    child, parent, url, payload = prepare(captured)
    with age_db() as s:
        row = s.scalar(select(PendingConsent))
        if binding_change == 'parent': row.parent_email = 'other@example.test'
        if binding_change == 'child': row.profile_id = 2
        if binding_change == 'director': row.director_email = 'injected@example.test'
        if binding_change == 'notice': row.notice_version = 'superseded'
        if binding_change == 'session': s.get(WoodchuckProfile, 1).session_version += 1
        s.commit()
    if binding_change == 'environment': monkeypatch.setenv('KWS_ENVIRONMENT', 'production')
    if binding_change == 'scope': monkeypatch.setenv('KWS_TEST_ORG_ID', 'other-org')
    assert response(parent, captured, payload).status_code in (400, 503)
    assert verification(age_db).state == 'accepted'


def test_concurrent_both_callback_methods_complete_once(age_db, captured):
    child, parent, url, payload = prepare(captured)
    barrier = threading.Barrier(6)
    def deliver(i):
        client = TestClient(app)
        barrier.wait()
        return (webhook(client, captured, event(payload)) if i % 2 else response(client, captured, payload)).status_code
    with ThreadPoolExecutor(max_workers=6) as workers:
        assert list(workers.map(deliver, range(6))) == [200] * 6
    assert verification(age_db).state == 'verified'
    with age_db() as s:
        row = s.scalar(select(PendingConsent))
        assert row.confirmed_at and row.activation_hash
        assert s.scalar(select(func.count(KWSVerification.id))) == 1


@pytest.mark.parametrize('order', ['withdraw-first', 'result-first', 'concurrent'])
def test_withdrawal_race_never_resurrects_permission(age_db, captured, order):
    child, parent, url, payload = prepare(captured)
    withdraw_url = path(captured[0], 'withdraw')
    csrf = parent.get(withdraw_url).context['csrf']
    cancel = lambda: parent.post(withdraw_url, data={'csrf': csrf, 'withdraw': 'yes'})
    deliver = lambda: webhook(TestClient(app), captured, event(payload))
    if order == 'concurrent':
        with ThreadPoolExecutor(max_workers=2) as workers:
            a, b = workers.submit(cancel), workers.submit(deliver)
            assert a.result().status_code == b.result().status_code == 200
    else:
        for action in ([cancel, deliver] if order == 'withdraw-first' else [deliver, cancel]):
            assert action().status_code == 200
    assert webhook(parent, captured, event(payload)).status_code == 200
    assert response(parent, captured, payload).status_code == 200
    assert verification(age_db).state == 'cancelled'
    assert parent.get(url).status_code == 409
    with age_db() as s: assert s.scalar(select(func.count(ConsentEvidence.id))) == 0
    assert child.get('/account/state').status_code == 403


def test_failed_expired_and_cross_request_replay(age_db, captured):
    child, parent, url, payload = prepare(captured, existing=False)
    assert response(parent, captured, payload, verified=False).status_code == 200
    assert response(parent, captured, payload, verified=True).status_code == 400
    assert verification(age_db).state == 'failed'
    child2, parent2, url2, payload2 = prepare(captured, existing=False, tag='b')
    assert response(parent2, captured, payload2).status_code == 400  # Transaction already used by another request.
    captured[1][0] += timedelta(hours=49)
    assert response(parent2, captured, payload2, transaction='new-transaction').status_code == 200
    with age_db() as s:
        assert s.scalar(select(KWSVerification).where(KWSVerification.payload_hash == hash_invitation_token(payload2))).state == 'cancelled'


@pytest.mark.parametrize('existing', [True, False])
def test_account_only_free_journey_parent_session_and_retained_entitlements(age_db, captured, existing):
    before = state(age_db)
    child, parent, pid, wid = authorize(age_db, captured, sharing=False, existing=existing)
    assert save(child, review=False, note='Private account-only practice').status_code == 303
    assert child.get('/family/practice/data').json()['director'] is None
    assert len(child.get('/family/practice/data').json()['charts']) == 1
    assert parent.get('/family/parent/data').status_code == 403
    parent = open_parent(parent, captured, wid)
    info = parent.get('/family/parent/data').json()
    assert info['permissions'] == [] and 'insights' not in info['metrics']
    assert state(age_db)[1]['practiceLog'] == before[1]['practiceLog']
    with age_db() as s:
        assert s.scalar(select(func.count(DirectorPermission.id))) == 0
        assert s.scalar(select(func.count(Membership.id))) == 0
        assert s.scalar(select(KWSVerification)).guardian_attested_at
    assert post(parent, '/family/parent/withdraw', {'withdraw': 'yes'}, page='/family/parent').status_code == 200
    assert child.get('/family/practice/data').status_code in (401, 403)
    with age_db() as s: assert s.scalar(select(func.count(PracticeChart.id))) == 1


def test_director_revocation_and_withdrawal_survive_duplicate_callbacks(age_db, captured):
    child, parent, pid, wid = authorize(age_db, captured)
    director = connect(captured[0])
    parent = open_parent(parent, captured, wid)
    with age_db() as s: permission_id = s.scalar(select(DirectorPermission.id))
    assert post(parent, f'/family/parent/revoke-director/{permission_id}', {'confirm': 'yes'}, page='/family/parent').status_code == 303
    payload = kws.captured_requests[-1][1]
    assert response(parent, captured, payload, transaction='synthetic-a').status_code == 200
    assert director.get('/family/director/data').status_code == 403
    assert child.get('/family/practice/data').status_code == 200
    assert post(parent, '/family/parent/withdraw', {'withdraw': 'yes'}, page='/family/parent').status_code == 200
    assert webhook(parent, captured, event(payload, transaction='synthetic-a')).status_code == 200
    assert child.get('/family/practice/data').status_code in (401, 403)
    with age_db() as s:
        assert s.scalar(select(func.count(ConsentEvidence.id))) == 1
        assert s.scalar(select(func.count(DirectorPermission.id))) == 1
        assert s.scalar(select(ConsentEvidence)).withdrawn_at


@pytest.mark.parametrize('missing', ['guardian_attestation', 'notice_accepted'])
def test_verification_requires_guardian_and_current_notice(age_db, captured, missing):
    child, parent = TestClient(app), TestClient(app)
    assert post(child, '/family/request', {'parent_email': 'parent-a@example.test', 'confirm_account': 'new'}).status_code == 200
    fields = choices(); fields.pop(missing)
    assert post(parent, path(captured[0], 'approve'), fields).status_code == 409
    assert not kws.captured_requests
    with age_db() as s: assert s.scalar(select(func.count(KWSVerification.id))) == 0


def test_declining_account_does_not_send_kws_or_create_child(age_db, captured):
    child, parent, url, payload = prepare(captured, existing=False, fields={'notice_version': consent.NOTICE_VERSION})
    assert not kws.captured_requests and parent.get(url).status_code == 409
    with age_db() as s:
        assert s.scalar(select(func.count(WoodchuckProfile.id))) == 2
        assert s.scalar(select(func.count(KWSVerification.id))) == 0


def test_budget_is_durable_rolling_and_serialized(age_db, captured):
    barrier = threading.Barrier(4)
    def reserve(_):
        barrier.wait()
        with age_db() as s:
            try:
                kws.reserve_budget(s, 'synthetic-budget@example.test'); s.commit(); return True
            except ValueError:
                s.rollback(); return False
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert list(workers.map(reserve, range(4))).count(True) == 1
    for _ in range(2):
        captured[1][0] += timedelta(minutes=6)
        with age_db() as s: kws.reserve_budget(s, 'synthetic-budget@example.test'); s.commit()
    captured[1][0] += timedelta(minutes=6)
    with age_db() as s:
        with pytest.raises(ValueError): kws.reserve_budget(s, 'synthetic-budget@example.test')
    captured[1][0] += timedelta(hours=1)
    with age_db() as s: kws.reserve_budget(s, 'synthetic-budget@example.test'); s.commit()


def test_uncertain_delivery_is_not_retried_or_granted(age_db, captured, monkeypatch):
    attempts = []
    def unavailable(*args):
        attempts.append(True)
        raise kws_client.KWSUnavailable('Synthetic timeout')
    monkeypatch.setattr(kws.client, 'send_email', unavailable)
    child, parent, url, payload = prepare(captured)
    assert verification(age_db).state == 'delivery_unknown'
    assert post(parent, url, choices()).status_code == 409
    assert len(attempts) == 1
    with age_db() as s:
        assert s.scalar(select(KWSEmailBudget)).sent_at
        assert s.scalar(select(func.count(ConsentEvidence.id))) == 0


def test_oauth_contract_token_expiry_and_bounded_retry_without_email_flood(monkeypatch, captured):
    requests, sleeps, now = [], [], [100.0]
    monkeypatch.setattr(kws_client.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(kws_client.time, 'sleep', sleeps.append)
    responses = [(503, None), (429, None), (200, {'access_token': 'synthetic-bearer', 'expires_in': 60}),
                 (202, {'response': {'trustEmailRequestAccepted': True}}),
                 (202, {'response': {'trustEmailRequestAccepted': True}}),
                 (200, {'access_token': 'synthetic-refreshed', 'expires_in': 60}), (503, None)]
    def transport(url, headers, body):
        requests.append((url, headers, body))
        return responses.pop(0)
    monkeypatch.setattr(kws_client, 'transport', transport)
    client, cfg = kws_client.Client(), kws_client.Config.load()
    assert client.send_email(cfg, 'synthetic@example.test', 'x'*43)
    assert client.send_email(cfg, 'synthetic@example.test', 'y'*43)
    now[0] += 61
    with pytest.raises(kws_client.KWSUnavailable): client.send_email(cfg, 'synthetic@example.test', 'z'*43)
    assert sleeps == [0.25, 0.5] and not responses
    token = requests[0]
    assert token[0] == kws_client.AUTH_URL and token[1]['Authorization'].startswith('Basic ')
    assert parse_qs(token[2].decode()) == {'grant_type': ['client_credentials'], 'scope': ['verification']}
    sends = [r for r in requests if r[0] == kws_client.API_URL]
    assert len(sends) == 3 and all(r[1]['User-Agent'] for r in requests)
    assert sends[0][1]['Authorization'] == 'Bearer synthetic-bearer'
    assert json.loads(sends[0][2])['userContext'] == 'parent'
    assert json.loads(sends[0][2])['externalPayload'] == 'x'*43


def test_configuration_fails_closed_in_production(age_db, captured, monkeypatch):
    monkeypatch.setattr(consent, 'UNDER13_REVIEW_APPROVED', False)
    monkeypatch.setenv('APP_ENV', 'production')
    assert not consent.under13_available()
    assert 'name="parent_email"' not in TestClient(app).get('/family/request').text
    assert TestClient(app).get(RESPONSE).status_code == 503
    with pytest.raises(kws_client.KWSUnavailable): kws_client.Config.load()


def test_prototype_email_approval_is_disabled(age_db, captured):
    with age_db() as s:
        with pytest.raises(ValueError): consent.approve(s, 'x'*43, explicit='yes', notice_version=consent.NOTICE_VERSION, review_allowed='yes')


def test_activation_waiting_withdrawal_is_effective_after_activation(age_db, captured):
    """Hold activation's row lock until withdrawal is waiting, then create evidence."""
    child, parent, url, payload = prepare(captured)
    assert webhook(parent, captured, event(payload)).status_code == 200
    activation_token = parent.get(url).context['activation_token']
    withdraw_url = path(captured[0], 'withdraw')
    csrf = parent.get(withdraw_url).context['csrf']
    with age_db() as session:
        row = consent.pending_from_token(session, activation_token, purpose='activate', lock=True)
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(parent.post, withdraw_url, data={'csrf': csrf, 'withdraw': 'yes'})
            # No sleeps: finish the locked activation while the competing request
            # runs. Either start time must result in the completed evidence withdrawn.
            profile, evidence, permission = consent.activate(session, activation_token,
                                                            profile=session.get(WoodchuckProfile, 1), fields={})
            session.commit()
            assert future.result(timeout=10).status_code == 200
    with age_db() as session:
        assert session.get(ConsentEvidence, evidence.id).withdrawn_at
        assert session.scalar(select(KWSVerification)).activated_consent_id == evidence.id
    assert child.get('/family/practice/data').status_code in (401, 403)
    assert response(parent, captured, payload).status_code == 200
    assert child.get('/family/practice/data').status_code in (401, 403)


def test_sensitive_callback_payload_and_secrets_are_not_logged(age_db, captured, caplog):
    child, parent, url, payload = prepare(captured)
    assert response(parent, captured, payload).status_code == 200
    for sensitive in (payload, 'parent-a@example.test', WEB_SECRET, RED_SECRET, 'synthetic-api-key'):
        assert sensitive not in caplog.text


@pytest.mark.parametrize('existing', [True, False])
def test_permission_page_labels_original_subject(age_db, captured, existing):
    child, parent, url, payload = prepare(captured, existing=existing)
    result = parent.get(url)
    assert result.context['account_label'] == ('WC-AGE-A' if existing else 'one new Free child account')
    assert 'WC-AGE-B' not in result.text
