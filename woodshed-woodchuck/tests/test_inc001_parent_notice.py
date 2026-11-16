"""INC001 notice v4: synthetic requests, captured email and immutable evidence."""
from datetime import timedelta
import hashlib
import html
from html.parser import HTMLParser
import re
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import age_privacy, child_authorization as consent, kws_client
from app import kws_verification as kws
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence, DirectorPermission, PendingConsent
from app.email_service import DeliveryResult, EmailService, SMTPConfig
from app.kws_models import KWSVerification
from app.main import app
from app.models import WoodchuckProfile
from test_kws_production import choices, make_pending, make_profile, production_db


V3 = 'private-practice-kws-production-v3'
V3_SHA256 = '9024057b4a48a5743a43fb277aff95d84b082d02f2951d17c7179646d4c6e00a'
POLICY_URL = 'https://woodshed-woodchuck.onrender.com/privacy'


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.hrefs = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.append(dict(attrs).get('href'))


def values(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


@pytest.fixture
def notice_context(production_db, monkeypatch):
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', production_db)
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://testserver')
    emails, provider_calls = [], []

    class Capture(EmailService):
        def __init__(self):
            super().__init__(SMTPConfig('capture.invalid', 587, 'synthetic', 'synthetic',
                'operator@example.test', 'Woodshed', True))

        def send(self, message):
            emails.append(message)
            return DeliveryResult(True, 'captured')

    monkeypatch.setattr(consent, 'EmailService', Capture)

    def send(cfg, email, payload):
        provider_calls.append((cfg.environment, email, payload))
        return True

    monkeypatch.setattr(kws.client, 'send_email', send)
    return production_db, emails, provider_calls


def request_parent(client, *, sharing=False):
    page = client.get('/family/request')
    assert page.status_code == 200
    return client.post('/family/request', data={
        'csrf': page.context['csrf'], 'confirm_account': 'new',
        'parent_email': 'parent@example.test',
        **({'director_email': 'director@example.test', 'director_name': 'Synthetic Director'}
           if sharing else {}),
    })


def test_v4_notice_hash_and_expiry_are_distinct_from_cleanup():
    assert consent.PRODUCTION_NOTICE_VERSION == 'private-practice-kws-production-v4'
    assert consent.PRODUCTION_NOTICE_SHA256 == hashlib.sha256(
        consent.PRODUCTION_NOTICE.encode('utf-8')).hexdigest()
    assert consent.PRODUCTION_NOTICE_SHA256 != V3_SHA256
    assert consent.PENDING_TTL == timedelta(hours=48)
    assert consent.APPROVED_TTL == timedelta(hours=72)
    for sentence in (
        'Unanswered parent-permission requests expire after 48 hours.',
        "Woodshed collects the parent's contact information to request parental permission. If permission is not granted, Woodshed deletes unnecessary parent contact information and related request records within 30 days after the request expires.",
        'Expiration ends the permission request but does not immediately delete its records.',
        "This cleanup does not delete valid consent evidence associated with activated accounts. Such evidence is governed by Woodshed's separate consent-retention policy.",
        'Privacy Policy: ' + POLICY_URL,
    ):
        assert sentence in consent.PRODUCTION_NOTICE


def test_public_parent_notice_has_a_direct_policy_link(notice_context):
    _, emails, provider_calls = notice_context
    response = TestClient(app).get('/family/notice')
    assert response.status_code == 200
    assert '<a href="/privacy">Privacy Policy</a>' in response.text
    assert consent.PRODUCTION_NOTICE in html.unescape(response.text)
    assert not emails and not provider_calls


@pytest.mark.parametrize('sharing', [False, True])
def test_request_email_and_approval_link_policy_and_keep_explicit_choices(notice_context, sharing):
    factory, emails, provider_calls = notice_context
    client = TestClient(app)
    assert request_parent(client, sharing=sharing).status_code == 200
    assert len(emails) == 1 and emails[0]['To'] == 'parent@example.test'
    plain = emails[0].get_body(preferencelist=('plain',)).get_content()
    body = emails[0].get_body(preferencelist=('html',)).get_content()
    assert consent.PRODUCTION_NOTICE in plain
    assert 'Privacy Policy: ' + POLICY_URL in plain
    assert 'https://testserver/privacy' in Links(body).hrefs
    assert '>Privacy Policy</a>' in body
    assert all(html.escape(p) in body for p in consent.PRODUCTION_NOTICE.splitlines())
    approve = re.search(r'/family/approve/[^\s]+', plain).group()
    withdraw = re.search(r'/family/withdraw/[^\s]+', plain).group()
    assert 'https://testserver' + approve in Links(body).hrefs
    assert 'https://testserver' + withdraw in Links(body).hrefs
    response = client.get(approve)
    assert response.status_code == 200
    assert '<a href="/privacy">Privacy Policy</a>' in response.text
    assert response.text.index('href="/privacy"') < response.text.index('<details')
    assert consent.PRODUCTION_NOTICE in html.unescape(response.text)
    assert 'name="notice_version" value="private-practice-kws-production-v4"' in response.text
    for field in ('guardian_attestation', 'notice_accepted', 'account_allowed'):
        assert f'name="{field}"' in response.text
    assert not provider_calls  # Reviewing the notice is not KWS verification.
    with factory() as session:
        row = session.scalar(select(PendingConsent))
        assert row.director_email == ('director@example.test' if sharing else '')
        assert row.notice_version == consent.PRODUCTION_NOTICE_VERSION
        assert row.expires_at - row.created_at == timedelta(hours=48)
        assert session.scalar(select(func.count()).select_from(ConsentEvidence)) == 0
        assert session.scalar(select(func.count()).select_from(DirectorPermission)) == 0
        assert session.scalar(select(func.count()).select_from(WoodchuckProfile)) == 0


def test_old_pending_link_is_rejected_without_relabeling(notice_context):
    factory, emails, provider_calls = notice_context
    with factory() as session:
        profile = make_profile(session)
        pending, token = make_pending(session, profile)
        pending.notice_version = V3
        session.commit()
        session.refresh(pending)
        before = values(pending)
        ident = pending.id
    response = TestClient(app).get('/family/approve/' + token)
    assert response.status_code == 409
    with factory() as session:
        assert values(session.get(PendingConsent, ident)) == before
        assert session.scalar(select(ConsentEvidence)) is None
    assert not emails and not provider_calls


def test_old_submitted_version_cannot_start_verification(notice_context):
    factory, _, provider_calls = notice_context
    with factory() as session:
        profile = make_profile(session)
        pending, token = make_pending(session, profile)
        ident = pending.id
        session.refresh(pending)
        before = values(pending)
    client = TestClient(app)
    url = '/family/approve/' + token
    response = client.post(url, data={
        'csrf': client.get(url).context['csrf'], **choices(), 'notice_version': V3,
    })
    assert response.status_code == 409
    assert not provider_calls
    with factory() as session:
        assert values(session.get(PendingConsent, ident)) == before
        assert session.scalar(select(KWSVerification)) is None
        assert session.scalar(select(ConsentEvidence)) is None


@pytest.mark.parametrize('altered', ['version', 'digest'])
def test_verification_rejects_altered_notice_binding(notice_context, altered):
    factory, _, provider_calls = notice_context
    with factory() as session:
        profile = make_profile(session)
        pending, token = make_pending(session, profile)
        verification_id, _ = kws.start(session, token, choices())
        verification = session.get(KWSVerification, verification_id)
        assert verification.notice_sha256 == consent.PRODUCTION_NOTICE_SHA256
        if altered == 'version':
            pending.notice_version = V3
        else:
            verification.notice_sha256 = V3_SHA256
        with pytest.raises(kws.InvalidResult):
            kws.current_binding(session, pending, verification, kws_client.Config.load('production'))
        assert session.scalar(select(ConsentEvidence)) is None
        session.rollback()
    assert not provider_calls


@pytest.mark.parametrize('variant,expected', [('v3', True), ('wrong-hash', False), ('v4', True)])
def test_consent_eligibility_never_rewrites_historical_evidence(notice_context, variant, expected):
    factory, emails, provider_calls = notice_context
    with factory() as session:
        profile = make_profile(session)
        rule = age_privacy.declare_age(session, profile.id, 'under13')
        version = V3 if variant == 'v3' else consent.PRODUCTION_NOTICE_VERSION
        digest = consent.PRODUCTION_NOTICE_SHA256 if variant == 'v4' else V3_SHA256
        now = consent.clock()
        evidence = ConsentEvidence(profile_id=profile.id, parent_email='parent@example.test',
            notice_version=version, notice_sha256=digest, approved_at=now, confirmed_at=now)
        session.add(evidence)
        session.flush()
        rule.consent_id = evidence.id
        session.commit()
        for row in (evidence, profile, rule):
            session.refresh(row)
        before = values(evidence), values(profile), values(rule)
        assert consent.consent_active(session, rule) is expected
        assert age_privacy.eligible(session, profile.id) is expected
        session.commit()
        session.expire_all()
        assert (values(evidence), values(profile), values(rule)) == before
    assert not emails and not provider_calls
