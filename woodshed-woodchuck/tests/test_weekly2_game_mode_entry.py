"""Student-entry presentation and cohort/consent contracts on actual migrations."""
import base64
import json
import sys

from alembic import command
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import models as m
from app import tester_enrollments as testers
from tests.test_weekly2_foundation_migration import db, config, rows
from tests.test_tester_enrollments import account_form, CLAIMED


@pytest.fixture
def entry_runtime(db, monkeypatch):
    command.upgrade(config(), 'w25weekly002')
    from app.main import app
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(testers, 'clock', lambda: CLAIMED)
    monkeypatch.delenv('C001_REGISTRATION_DISABLED', raising=False)
    return db, factory, TestClient(app)


def test_student_entry_and_role_presentation_boundaries(entry_runtime, monkeypatch):
    db, factory, client = entry_runtime
    assert client.post('/account/create', data=account_form()).status_code == 200
    before = rows(db)
    home = client.get('/home')
    assert home.status_code == 200 and home.context['game_mode_entry'] is True
    assert 'id="game-mode-entry"' in home.text and 'data-auto="true"' in home.text
    assert rows(db) == before  # Presentation does not save, grant or enroll.
    for path in ['/privacy', '/account/help', '/setup?age=under13']:
        page = client.get(path)
        assert page.status_code == 200 and not page.context.get('game_mode_entry', False)
        assert 'id="game-mode-entry"' not in page.text
    from app import main
    monkeypatch.setattr(main, 'has_band_director_capability', lambda *a, **k: True)
    assert client.get('/home').context['game_mode_entry'] is False
    monkeypatch.setattr(main, 'has_band_director_capability', lambda *a, **k: False)
    cookie = client.cookies.get('session')
    signer = TimestampSigner(main.SESSION_SECRET)
    original = json.loads(base64.b64decode(signer.unsign(cookie)))
    for key in ['trusted_verifier_id', 'site_admin_fingerprint', 'contest_admin_token_fingerprint',
                'child_parent_consent', 'child_director_permission']:
        # Negative presentation fixtures; these values cannot authorize adult operations.
        session = {**original, key: 'synthetic-role-context'}
        client.cookies.clear()
        client.cookies.set('session', signer.sign(base64.b64encode(json.dumps(session).encode())).decode())
        assert client.get('/home').context['game_mode_entry'] is False
    assert rows(db) == before


def test_setup_and_guest_have_no_visible_musician_level(entry_runtime):
    db, factory, client = entry_runtime
    before_attempt = rows(db)
    assert client.post('/account/create', data=account_form('under13')).status_code == 403
    before = rows(db)
    for table in before_attempt:
        if table != 'c001_abuse_events':assert before[table] == before_attempt[table], table
    assert len(before['c001_abuse_events']) == 2
    assert {r['kind'] for r in before['c001_abuse_events']} == {'creation', 'age_rejected'}
    assert all(r['profile_id'] is None and r['occurrences'] == 1 for r in before['c001_abuse_events'])
    setup = client.get('/setup?age=adult')
    assert setup.status_code == 200
    assert '<input type="hidden" name="level" value="Beginner"' in setup.text
    assert 'name="game_mode" required' in setup.text and 'Choose a Game Mode' in setup.text
    assert '<label for="level">' not in setup.text
    pending = client.get('/setup?age=under13')
    assert 'id="account-create-form"' not in pending.text and 'id="game-mode-entry"' not in pending.text
    guest = client.get('/guest')
    assert 'id="guest-game_mode"' in guest.text and 'id="guest-level"' not in guest.text
    assert "connect-src 'none'" in guest.headers['content-security-policy']
    assert rows(db) == before


@pytest.mark.parametrize('cohort', ['C001', 'C002'])
def test_cohort_creation_then_explicit_mode_preserves_attribution(entry_runtime, monkeypatch, cohort):
    db, factory, _ = entry_runtime
    from tests import test_tester_enrollments as original
    if cohort == 'C001':
        original.test_c001_13plus_creation_is_atomic_full_and_does_not_touch_memberships(
            factory, '/prebeta/C001?entry=secret-symbol', 'DIRECTOR1', '13to17')
    else:
        original.test_c002_context_survives_guest_registration_with_lifetime_access_and_distinct_analytics(factory)
    with factory() as session:
        enrollment = session.scalar(select(m.TesterEnrollment).where(m.TesterEnrollment.cohort_key == cohort))
        profile = session.get(m.WoodchuckProfile, enrollment.profile_id)
        wid = profile.woodchuck_id
        assert profile.game_mode is None and profile.level == 'Beginner'
    from app.main import app
    client = TestClient(app)
    assert client.post('/account/login', data={'woodchuck_id': wid, 'pin': '2468'}).status_code == 200
    before = rows(db)
    assert client.patch('/account/profile/game-mode', json={'game_mode': 'Challenge'}).status_code == 200
    after = rows(db)
    for table in before:
        if table not in ('woodchuck_profiles', 'woodchuck_states'):
            assert after[table] == before[table], table
    with factory() as session:
        assert session.scalar(select(m.WoodchuckProfile).where(m.WoodchuckProfile.woodchuck_id == wid)).game_mode == 'Challenge'
    assert client.post('/account/create', data=account_form()).status_code == 400
    assert rows(db)['woodchuck_profiles'] == after['woodchuck_profiles']


def test_c002_still_independent_of_c001_activation(entry_runtime, monkeypatch):
    _, factory, _ = entry_runtime
    from tests.test_tester_enrollments import test_c002_does_not_use_c001_activation_control_or_cap
    test_c002_does_not_use_c001_activation_control_or_cap(factory, monkeypatch)


def test_child_defers_until_authorized_student_entry(entry_runtime, monkeypatch):
    db, factory, _ = entry_runtime
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(
        factory, monkeypatch, '/prebeta/C002', 'C002', 'DIRECTOR1')
    from app.main import app
    from app import child_authorization as consent
    from app.child_models import ConsentEvidence
    with factory() as session:
        profile = session.scalar(select(m.WoodchuckProfile))
        wid, ident = profile.woodchuck_id, profile.id
        assert profile.game_mode is None
    child = TestClient(app)
    assert child.post('/account/login', data={'woodchuck_id': wid, 'pin': '2468'}).status_code == 200
    assert child.get('/home').context['game_mode_entry'] is True
    assert child.patch('/account/profile/game-mode', json={'game_mode': 'Performance'}).status_code == 200
    with factory() as session:
        consent.withdraw_evidence(session, session.scalar(select(ConsentEvidence)))
        session.commit()
    assert child.patch('/account/profile/game-mode', json={'game_mode': 'Beginner'}).status_code == 401
    with factory() as session:
        assert session.get(m.WoodchuckProfile, ident).game_mode == 'Performance'
