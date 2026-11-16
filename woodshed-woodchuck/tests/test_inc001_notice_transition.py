"""Production-policy transition with synthetic KWS and isolated PostgreSQL only."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import age_privacy, child_authorization as consent, kws_client, kws_retention, parent_access
from app import kws_verification as kws
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence, DirectorPermission, ParentAccess, PendingConsent
from app.db import Base
from app.kws_models import KWSEmailBudget, KWSVerification
from app.main import app
from app.models import WoodchuckProfile
from app.security import generate_invitation_token, hash_invitation_token
from test_age_screening import age_db
from test_private_practice import captured
from test_kws_production import (PRODUCTION_ENV, TEST_ENV, choices, configure, make_pending,
                                 signed_production_webhook)
from test_inc001_parent_notice import Links, V3, V3_SHA256
from test_kws_retention import pending, verification


@pytest.fixture
def transition_context(age_db, captured, monkeypatch):
    configure(monkeypatch, PRODUCTION_ENV)
    return age_db, captured


@contextmanager
def original_v3(monkeypatch):
    # Exact original notice from base 57d29d9, not a relabeled v4 notice.
    notice = (Path(__file__).parent / 'fixtures/inc001-production-v3-notice.txt').read_text()
    assert hashlib.sha256(notice.encode()).hexdigest() == V3_SHA256
    with monkeypatch.context() as old:
        old.setattr(consent, 'PRODUCTION_NOTICE_VERSION', V3)
        old.setattr(consent, 'PRODUCTION_NOTICE', notice)
        old.setattr(consent, 'PRODUCTION_NOTICE_SHA256', V3_SHA256)
        yield


def original_flow(factory, *, stage='activated'):
    """Use real application functions; only delivery/provider results are mocked."""
    with factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        age_privacy.declare_age(session, profile.id, 'under13')
        row, token = make_pending(session, profile)
        result = {'pending': row.id, 'approve': token, 'profile': profile.id}
        if stage == 'requested':
            return result
        vid, payload = kws.start(session, token, {
            **choices(), 'director_allowed': 'yes', 'review_allowed': 'yes',
            'director_email': 'director@example.test', 'director_name': 'Synthetic Director',
        })
        assert kws.deliver(session, vid, payload) == 'accepted'
        result.update(verification=vid, payload=payload)
        if stage == 'accepted':
            return result
        cfg = kws_client.Config.load('production')
        raw, signature = signed_production_webhook(cfg, payload)
        # Real signature parser + completion; no network call or real email.
        activation = kws.complete(session, cfg, *kws.webhook_result(raw, signature, cfg))
        session.commit()
        result['activate'] = activation
        if stage == 'verified':
            return result
        profile, evidence, permission = consent.activate(session, activation, profile=profile, fields={})
        session.commit()
        result.update(consent=evidence.id, director=permission.id)
        return result


def snapshot(factory):
    with factory() as session:
        return {table.name: [dict(row) for row in session.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()]
            for table in Base.metadata.sorted_tables}


@pytest.mark.parametrize('original_version', ['v3', 'v4'])
def test_actual_activation_retains_original_authority_and_all_evidence(
        transition_context, monkeypatch, original_version):
    factory, (mail, now) = transition_context
    if original_version == 'v3':
        with original_v3(monkeypatch):
            ids = original_flow(factory)
    else:
        ids = original_flow(factory)
    before = snapshot(factory)
    # The pending activation link ends at activation; it is not consent expiry.
    now[0] += timedelta(days=40)
    with factory() as session:
        rule = session.get(AccountPrivacy, ids['profile'])
        evidence = session.get(ConsentEvidence, ids['consent'])
        assert (evidence.notice_version, evidence.notice_sha256) == (
            (V3, V3_SHA256) if original_version == 'v3' else
            (consent.PRODUCTION_NOTICE_VERSION, consent.PRODUCTION_NOTICE_SHA256))
        assert session.get(KWSVerification, ids['verification']).state == 'activated'
        assert consent.consent_active(session, rule)
        assert age_privacy.eligible(session, ids['profile'])
        assert parent_access.valid_consent(session, ids['consent'])[0].id == evidence.id
        assert consent.permission_valid(session, session.get(DirectorPermission, ids['director']))
        with pytest.raises(ValueError):
            consent.activate(session, ids['activate'], profile=session.get(WoodchuckProfile, 1), fields={})
        # Even overdue request cleanup cannot delete an activated KWS chain.
        assert kws_retention.cleanup(session, now=now[0], apply=True)['pending_consents'] == 0
        session.rollback()  # Budget pruning is unrelated to the evidence assertion.
    assert snapshot(factory) == before
    assert not mail  # No transition notification, replacement consent or automatic resend.


@pytest.mark.parametrize('invalid', ['withdrawn', 'deleted', 'wrong-profile', 'wrong-digest',
                                     'wrong-version', 'superseded', 'missing-email', 'closed-runtime',
                                     'test-environment'])
def test_v3_exception_never_revives_invalid_authority(transition_context, monkeypatch, invalid):
    factory, (_, now) = transition_context
    with original_v3(monkeypatch):
        ids = original_flow(factory)
    with factory() as session:
        evidence = session.get(ConsentEvidence, ids['consent'])
        rule = session.get(AccountPrivacy, 1)
        if invalid == 'withdrawn':
            consent.withdraw_evidence(session, evidence)
        elif invalid == 'deleted':
            session.get(WoodchuckProfile, 1).status = 'deleted'
        elif invalid == 'wrong-profile':
            evidence.profile_id = 2
        elif invalid == 'wrong-digest':
            evidence.notice_sha256 = consent.PRODUCTION_NOTICE_SHA256
        elif invalid == 'wrong-version':
            evidence.notice_version = 'private-practice-kws-production-v2'
        elif invalid == 'superseded':
            rule.consent_id = None
        elif invalid == 'missing-email':
            evidence.parent_email = None
        elif invalid == 'closed-runtime':
            monkeypatch.setenv('KWS_PRODUCTION_ENABLED', 'false')
        elif invalid == 'test-environment':
            configure(monkeypatch, TEST_ENV)
        session.commit()
    before = snapshot(factory)
    with factory() as session:
        assert not age_privacy.eligible(session, 1)
        assert not consent.permission_valid(session, session.get(DirectorPermission, ids['director']))
        with pytest.raises(ValueError):
            parent_access.valid_consent(session, ids['consent'])
    assert snapshot(factory) == before


def test_v3_revoked_director_and_expired_parent_access_remain_invalid(transition_context, monkeypatch):
    factory, (_, now) = transition_context
    with original_v3(monkeypatch):
        ids = original_flow(factory)
    token = generate_invitation_token()
    with factory() as session:
        permission = session.get(DirectorPermission, ids['director'])
        permission.revoked_at = now[0]
        session.add(ParentAccess(consent_id=ids['consent'], link_hash=hash_invitation_token(token),
                                link_expires_at=now[0]))
        session.commit()
    before = snapshot(factory)
    with factory() as session:
        assert age_privacy.eligible(session, 1)  # Only the withdrawn/expired scope is denied.
        assert not consent.permission_valid(session, session.get(DirectorPermission, ids['director']))
        with pytest.raises(ValueError, match='expired'):
            parent_access.consume_link(session, token)
    assert snapshot(factory) == before


@pytest.mark.parametrize('stage', ['requested', 'accepted', 'verified'])
def test_pending_v3_cannot_activate_or_reuse_kws_as_v4(transition_context, monkeypatch, stage):
    factory, (mail, _) = transition_context
    with original_v3(monkeypatch):
        ids = original_flow(factory, stage=stage)
    before = snapshot(factory)
    client = TestClient(app)
    assert client.get('/family/approve/' + ids['approve']).status_code == 409
    with factory() as session:
        with pytest.raises(ValueError):
            kws.start(session, ids['approve'], choices())
        if stage == 'verified':
            with pytest.raises(ValueError):
                consent.activate(session, ids['activate'], profile=session.get(WoodchuckProfile, 1), fields={})
        elif stage == 'accepted':
            cfg = kws_client.Config.load('production')
            raw, signature = signed_production_webhook(cfg, ids['payload'])
            with pytest.raises(kws.InvalidResult):
                kws.complete(session, cfg, *kws.webhook_result(raw, signature, cfg))
        assert session.scalar(select(ConsentEvidence)) is None
    assert snapshot(factory) == before
    assert not mail
    # Only a deliberate fresh request sends a new notice. Old evidence is kept.
    with factory() as session:
        fresh = consent.request_consent(session, parent_email='parent@example.test',
                                        profile=session.get(WoodchuckProfile, 1))
        session.commit()
        assert fresh.id != ids['pending'] and fresh.notice_version == consent.PRODUCTION_NOTICE_VERSION
        assert session.scalar(select(KWSVerification).where(KWSVerification.pending_id == fresh.id)) is None
    after = snapshot(factory)
    assert before[KWSVerification.__tablename__] == after[KWSVerification.__tablename__]
    assert before['child_pending_consents'][0] == next(
        r for r in after['child_pending_consents'] if r['id'] == ids['pending'])
    assert len(mail) == 1


def test_request_is_unusable_at_exact_48_hour_expiry(transition_context):
    factory, (_, now) = transition_context
    ids = original_flow(factory, stage='requested')
    with factory() as session:
        now[0] = consent.utc(session.get(PendingConsent, ids['pending']).expires_at)
    before = snapshot(factory)
    with factory() as session:
        with pytest.raises(ValueError, match='expired'):
            kws.start(session, ids['approve'], choices())
    assert snapshot(factory) == before


def test_notice_and_email_privacy_targets_are_public(transition_context):
    factory, (mail, _) = transition_context
    with factory() as session:
        consent.request_consent(session, parent_email='parent@example.test')
        session.commit()
    client = TestClient(app)
    assert client.get('/family/notice').status_code == 200
    body = mail[0].get_body(preferencelist=('html',)).get_content()
    policy = next(url for url in Links(body).hrefs if url.endswith('/privacy'))
    response = client.get(policy, follow_redirects=False)
    assert response.status_code == 200
    assert 'Privacy Policy' in response.text
    assert client.get('/account/me').json()['authenticated'] is False


def test_scheduled_module_applies_cleanup_in_fresh_postgres_process(transition_context):
    factory, (_, now) = transition_context
    ids = original_flow(factory)
    # Use real wall clock for the fresh CLI, which does not inherit monkeypatches.
    current = datetime.now(timezone.utc)
    with factory() as session:
        protected = session.get(PendingConsent, ids['pending'])
        protected.expires_at = current - timedelta(days=90)
        expired = []
        for index, state in enumerate([None, 'reserved', 'accepted', 'delivery_unknown', 'failed', 'cancelled', 'verified']):
            tag = 'unanswered-' + str(index)
            row = pending(session, tag, expires_at=current - timedelta(days=29, hours=12))
            expired.append(row.id)
            if state:
                verification(session, row, chr(97 + index), state,
                             completed_at=current - timedelta(days=29, hours=12))
        recent = pending(session, 'recent', expires_at=current - timedelta(hours=1))
        unknown = pending(session, 'unknown', expires_at=current - timedelta(days=50))
        verification(session, unknown, 'u', 'unsupported-state')
        session.add(KWSEmailBudget(email_hash='synthetic-old-budget', sent_at=[current.timestamp() - 7200]))
        session.add(KWSEmailBudget(email_hash='synthetic-mixed-budget', sent_at=[current.timestamp() - 7200, current.timestamp() - 60]))
        session.commit()
        schema = session.scalar(text('select current_schema()'))
        assert schema.startswith('age_test_')
    before = snapshot(factory)
    url = factory.kw['bind'].url
    # Use only the explicitly supplied disposable test socket, not inherited env.
    assert url.drivername == 'postgresql+psycopg' and not url.host
    assert str(url.query['host']).startswith('/tmp/inc001-notice-pg-')
    env = {'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
           'DATABASE_URL': url.render_as_string(hide_password=False),
           'PGOPTIONS': '-c search_path=' + schema + ' -c statement_timeout=5000',
           'SESSION_SECRET': 'synthetic-cleanup-cli-only'}
    def run(mode):
        result = subprocess.run([sys.executable, '-B', '-m', 'app.kws_retention', mode],
                                env=env, cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr + result.stdout
        report = json.loads(result.stdout)
        assert set(report) == {'pending_consents', 'kws_verifications', 'email_budget_rows', 'email_budget_timestamps'}
        assert all(type(n) is int for n in report.values())
        return report
    dry = run('--dry-run')
    assert dry['pending_consents'] == 7 and dry['kws_verifications'] == 6
    assert snapshot(factory) == before
    report = run('--apply')
    assert report == dry
    after = snapshot(factory)
    changed = {PendingConsent.__tablename__, KWSVerification.__tablename__, KWSEmailBudget.__tablename__}
    assert {k: v for k, v in before.items() if k not in changed} == {
        k: v for k, v in after.items() if k not in changed}
    with factory() as session:
        assert all(session.get(PendingConsent, ident) is None for ident in expired)
        assert session.get(PendingConsent, recent.id) and session.get(PendingConsent, unknown.id)
        assert session.get(ConsentEvidence, ids['consent'])
        assert session.get(KWSVerification, ids['verification']).state == 'activated'
        assert session.get(KWSEmailBudget, 'synthetic-old-budget') is None
        assert session.get(KWSEmailBudget, 'synthetic-mixed-budget').sent_at == [current.timestamp() - 60]
    assert all(n == 0 for n in run('--apply').values())
    assert snapshot(factory) == after
