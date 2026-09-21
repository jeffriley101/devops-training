"""Small corrections for the proposed isolated HTTPS Test setup; no real traffic."""
import json
from datetime import datetime, timezone
from email.message import EmailMessage
import pytest
from app import kws_client, kws_test_runtime
from app.email_service import EmailService, SMTPConfig
from test_private_practice import captured
from test_age_screening import age_db
from test_kws_verification import prepare, event, webhook


@pytest.mark.parametrize('location', ['US', 'US-NY', 'GB', 'AD-07', 'GB-ENG'])
def test_location_is_one_json_string_without_country_default(monkeypatch, captured, location):
    monkeypatch.setenv('KWS_TEST_LOCATION_JSON', json.dumps(location))
    cfg = kws_client.Config.load()
    calls = []
    client = kws_client.Client()
    monkeypatch.setattr(client, 'token', lambda _: 'synthetic-token')
    monkeypatch.setattr(kws_client, 'transport', lambda url, headers, body:
                        (calls.append(json.loads(body)) or (202, {'response': {'trustEmailRequestAccepted': True}})))
    client.send_email(cfg, 'synthetic@example.test', 'x'*43)
    assert calls[0]['location'] == location and isinstance(calls[0]['location'], str)


@pytest.mark.parametrize('location', ['US', '{}', '{"country":"US"}', 'null', '""',
                                      '"GB or AD-07"', '"us"', '" US "', '[]'])
def test_invalid_location_fails_closed(monkeypatch, captured, location):
    monkeypatch.setenv('KWS_TEST_LOCATION_JSON', location)
    with pytest.raises(kws_client.KWSUnavailable): kws_client.Config.load()


@pytest.mark.parametrize('allowlist', [None, '[]', '{}', '["parent@example.test,other@example.test"]'])
def test_missing_or_invalid_recipient_list_disables_configuration(monkeypatch, captured, allowlist):
    if allowlist is None: monkeypatch.delenv('KWS_TEST_RECIPIENTS_JSON')
    else: monkeypatch.setenv('KWS_TEST_RECIPIENTS_JSON', allowlist)
    with pytest.raises(kws_client.KWSUnavailable): kws_client.Config.load()


def test_kws_unlisted_recipient_is_blocked_before_oauth(monkeypatch, captured):
    client = kws_client.Client()
    monkeypatch.setattr(client, 'token', lambda _: pytest.fail('OAuth must not be requested'))
    with pytest.raises(kws_client.KWSUnavailable):
        client.send_email(kws_client.Config.load(), 'unlisted@example.test', 'x'*43)


@pytest.mark.parametrize('extra_header', ['To', 'Cc', 'Bcc', 'Resent-To', 'Resent-Cc', 'Resent-Bcc'])
def test_smtp_blocks_every_unlisted_envelope_recipient(monkeypatch, captured, extra_header):
    message = EmailMessage()
    message['To'] = 'synthetic@example.test'
    if extra_header == 'To': message.replace_header('To', 'unlisted@example.test')
    else: message[extra_header] = 'unlisted@example.test'
    config = SMTPConfig('capture.invalid', 587, 'synthetic', 'synthetic', 'operator@example.test', 'Test', True)
    service = EmailService(config, smtp_factory=lambda *a, **k: pytest.fail('SMTP must not connect'))
    assert service.send(message).code == 'test_recipient_blocked'


def test_smtp_allowed_adult_and_non_test_behavior_use_existing_transport(monkeypatch, captured):
    sent = []
    class SMTP:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ehlo(self): pass
        def login(self, *a): pass
        def send_message(self, message): sent.append(message)
    config = SMTPConfig('capture.invalid', 2525, 'synthetic', 'synthetic', 'operator@example.test', 'Test', False)
    service = EmailService(config, smtp_factory=lambda *a, **k: SMTP())
    message = EmailMessage(); message['To'] = 'Designated adult <synthetic@example.test>'
    assert service.send(message).sent
    monkeypatch.setenv('APP_ENV', 'production')
    message.replace_header('To', 'unlisted@example.test')
    assert service.send(message).sent and len(sent) == 2


@pytest.mark.parametrize('timestamp', ['1621535329', '01621535329'])
def test_webhook_epoch_seconds_uses_original_timestamp_text(age_db, captured, timestamp):
    captured[1][0] = datetime.fromtimestamp(1621535329, timezone.utc)
    _, parent, _, payload = prepare(captured)
    assert webhook(parent, captured, event(payload), timestamp=timestamp).status_code == 200


@pytest.fixture
def isolated_runtime(monkeypatch):
    for name, value in {'APP_ENV':'kws-test', 'KWS_ENVIRONMENT':'test',
        'KWS_TEST_DATABASE_CONFIRMED':'true',
        'DATABASE_URL':'postgresql+psycopg://synthetic:synthetic@isolated.invalid/woodshed_kws_test',
        'KWS_TEST_DATABASE_HOST':'isolated.invalid', 'SESSION_SECRET':'synthetic-session-only-32characters',
        'SESSION_COOKIE_SECURE':'true', 'PUBLIC_BASE_URL':'https://woodshed-kws-test-20260918.onrender.com',
        'KWS_TEST_RECIPIENTS_JSON':'["synthetic@example.test"]'}.items(): monkeypatch.setenv(name, value)


def test_runtime_preflight_has_no_database_connection(isolated_runtime):
    kws_test_runtime.validate()


@pytest.mark.parametrize('name,value', [('APP_ENV','production'),
    ('KWS_ENVIRONMENT','production'), ('KWS_TEST_DATABASE_CONFIRMED','false'),
    ('DATABASE_URL','postgresql://synthetic@other.invalid/woodshed_kws_test'),
    ('DATABASE_URL','postgresql://synthetic@isolated.invalid/production'),
    ('DATABASE_URL','sqlite:///synthetic.db'), ('SESSION_SECRET','short'),
    ('SESSION_COOKIE_SECURE','false'), ('PUBLIC_BASE_URL','https://production.onrender.com'),
    ('KWS_TEST_RECIPIENTS_JSON','[]')])
def test_runtime_rejects_wrong_database_and_instance_before_connect(isolated_runtime, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match='no database connection attempted'): kws_test_runtime.validate()


@pytest.fixture
def local_runtime(isolated_runtime, monkeypatch, tmp_path):
    monkeypatch.setenv('KWS_TEST_RUNTIME_MODE', 'local')
    monkeypatch.setenv('KWS_TEST_DATABASE_SOCKET', str(tmp_path))
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://postgres@/woodshed_kws_test?host='+str(tmp_path))
    monkeypatch.setenv('KWS_TEST_PUBLIC_HOST', 'synthetic.lhr.life')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://synthetic.lhr.life')
    monkeypatch.setenv('KWS_TEST_RECIPIENTS_JSON', '[]')
    monkeypatch.setenv('KWS_TEST_OUTBOUND_ENABLED', 'false')


def test_local_setup_allows_only_pinned_socket_and_tunnel_while_outbound_closed(local_runtime):
    kws_test_runtime.validate()


@pytest.mark.parametrize('url', ['postgresql://synthetic@production.invalid/woodshed_kws_test',
    'postgresql://synthetic@127.0.0.1/woodshed_kws_test',
    'postgresql+psycopg://postgres@/production?host=/tmp',
    'postgresql+psycopg://postgres@/woodshed_kws_test?host=/tmp', 'sqlite:///synthetic.db'])
def test_local_mode_rejects_all_tcp_wrong_socket_or_other_database(local_runtime, monkeypatch, url):
    monkeypatch.setenv('DATABASE_URL', url)
    with pytest.raises(ValueError, match='no database connection attempted'): kws_test_runtime.validate_database()


def test_local_mode_rejects_unpinned_public_origin(local_runtime, monkeypatch):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://production.invalid')
    with pytest.raises(ValueError): kws_test_runtime.validate()


def test_local_mode_cannot_enable_outbound_without_designated_recipient(local_runtime, monkeypatch):
    monkeypatch.setenv('KWS_TEST_OUTBOUND_ENABLED', 'true')
    with pytest.raises(ValueError): kws_test_runtime.validate()


def test_local_mode_blocks_kws_and_smtp_even_with_allowlisted_recipient(local_runtime, captured, monkeypatch):
    monkeypatch.setenv('KWS_TEST_RECIPIENTS_JSON', '["synthetic@example.test"]')
    client = kws_client.Client()
    monkeypatch.setattr(client, 'token', lambda _: pytest.fail('OAuth must not be requested'))
    with pytest.raises(kws_client.KWSUnavailable):
        client.send_email(kws_client.Config.load(), 'synthetic@example.test', 'x'*43)
    config=SMTPConfig('capture.invalid',587,'synthetic','synthetic','operator@example.test','Test',True)
    message=EmailMessage();message['To']='synthetic@example.test'
    service=EmailService(config,smtp_factory=lambda *a,**k:pytest.fail('SMTP must not connect'))
    assert service.send(message).code=='test_recipient_blocked'


@pytest.mark.parametrize('policy,mode', [({'enabled':True,'recipient':['a@example.test','b@example.test']},0o600),
    ({'enabled':True,'recipient':'a@example.test,b@example.test'},0o600),
    ({'enabled':True,'recipient':'a@example.test','extra':True},0o600),
    ({'enabled':False,'recipient':'a@example.test'},0o600),
    ({'enabled':True,'recipient':'a@example.test'},0o644)])
def test_local_delivery_invalid_or_shared_policy_cannot_open_mail_gate(monkeypatch, tmp_path, policy, mode):
    from app import kws_test_safety as safety
    socket=tmp_path/'socket';socket.mkdir()
    file=tmp_path/'delivery-authorization.json';file.write_text(json.dumps(policy));file.chmod(mode)
    monkeypatch.setenv('KWS_TEST_RUNTIME_MODE','local');monkeypatch.setenv('APP_ENV','kws-test')
    monkeypatch.setenv('KWS_ENVIRONMENT','test');monkeypatch.setenv('KWS_TEST_DATABASE_SOCKET',str(socket))
    monkeypatch.setenv('KWS_TEST_OUTBOUND_ENABLED','true');monkeypatch.setenv('KWS_TEST_RECIPIENTS_JSON','["a@example.test"]')
    safety.bootstrap_local_delivery()
    assert not safety.outbound_enabled() and not safety.allowed_recipients()


def test_local_delivery_only_one_authorized_adult_and_no_other_envelope_recipient(monkeypatch, tmp_path):
    from app import kws_test_safety as safety
    socket=tmp_path/'socket';socket.mkdir()
    file=tmp_path/'delivery-authorization.json';file.write_text(json.dumps({'enabled':True,'recipient':'a@example.test'}));file.chmod(0o600)
    monkeypatch.setenv('KWS_TEST_RUNTIME_MODE','local');monkeypatch.setenv('APP_ENV','kws-test')
    monkeypatch.setenv('KWS_ENVIRONMENT','test');monkeypatch.setenv('KWS_TEST_DATABASE_SOCKET',str(socket))
    safety.bootstrap_local_delivery()
    assert safety.outbound_enabled() and safety.allowed_recipients()=={'a@example.test'}
    message=EmailMessage();message['To']='a@example.test';assert safety.message_allowed(message)
    message['Bcc']='b@example.test';assert not safety.message_allowed(message)
    monkeypatch.setenv('KWS_TEST_ENABLED','true');monkeypatch.setenv('KWS_TEST_DATABASE_CONFIRMED','true')
    client=kws_client.Client();monkeypatch.setattr(client,'token',lambda _:pytest.fail('Unlisted email must not request OAuth'))
    with pytest.raises(kws_client.KWSUnavailable):client.send_email(None,'b@example.test','x'*43)


def test_local_delivery_missing_policy_closes_gate_and_does_not_change_nonlocal(monkeypatch,tmp_path):
    from app import kws_test_safety as safety
    monkeypatch.setenv('KWS_TEST_RUNTIME_MODE','local');monkeypatch.setenv('APP_ENV','kws-test')
    monkeypatch.setenv('KWS_ENVIRONMENT','test');monkeypatch.setenv('KWS_TEST_DATABASE_SOCKET',str(tmp_path/'socket'))
    monkeypatch.setenv('KWS_TEST_OUTBOUND_ENABLED','true');monkeypatch.setenv('KWS_TEST_RECIPIENTS_JSON','["a@example.test"]')
    safety.bootstrap_local_delivery();assert not safety.outbound_enabled() and not safety.allowed_recipients()
    monkeypatch.delenv('KWS_TEST_RUNTIME_MODE');monkeypatch.setenv('KWS_TEST_RECIPIENTS_JSON','["a@example.test"]')
    safety.bootstrap_local_delivery();assert safety.allowed_recipients()=={'a@example.test'}


@pytest.mark.parametrize('status', [200,400])
def test_local_delivery_callback_evidence_never_retains_signature_or_payload(monkeypatch,tmp_path,status):
    import asyncio
    from app.kws_test_safety import LocalCallbackEvidence
    monkeypatch.setenv('KWS_TEST_DATABASE_SOCKET',str(tmp_path/'socket'))
    async def app(scope,receive,send):
        await send({'type':'http.response.start','status':status,'headers':[]})
        await send({'type':'http.response.body','body':b'synthetic'})
    sent=[]
    async def send(message):sent.append(message)
    scope={'type':'http','method':'GET','path':'/family/kws/test/response',
        'query_string':b'signature=secret-synthetic-signature&externalPayload=secret-capability'}
    asyncio.run(LocalCallbackEvidence(app)(scope,None,send))
    file=tmp_path/'callback-deliveries.jsonl'
    assert sent[0]['status']==status
    if status==200:
        record=json.loads(file.read_text());assert set(record)=={'timestamp','method','path','status'}
        assert 'secret' not in file.read_text() and file.stat().st_mode & 0o077 == 0
    else:assert not file.exists()
