"""Guest route isolation and server-side account binding; synthetic SQLite only."""
from datetime import date
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import Base
from app.models import WoodchuckProfile, WoodchuckState
from app.security import hash_pin
from app.age_privacy import declare_age


@pytest.fixture
def guest_db(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    with factory() as session:
        for label, credits in [('A', 37), ('B', 83)]:
            profile = WoodchuckProfile(woodchuck_id='WC-GUEST-'+label, display_name='Synthetic '+label,
                pin_hash=hash_pin('2468'), instrument='Flute', level='Beginner', goal='Practice every day')
            session.add(profile)
            session.flush()
            declare_age(session, profile.id, 'adult')
            session.add(WoodchuckState(profile_id=profile.id, revision=7, state_json={
                'account': {'woodchuckId': profile.woodchuck_id, 'authenticated': True, 'serverRevision': 7},
                'profile': {'woodchuckName': profile.display_name, 'instrument':'Flute', 'level':'Beginner', 'goal':'Practice every day'},
                'progress': {'credits': credits}, 'practiceLog': [{'note': 'saved account '+label}],
            }))
        session.commit()
    yield factory
    engine.dispose()


def counts(factory):
    with factory() as session:
        return {table.name: session.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}


def test_guest_gets_never_bootstrap_accounts_set_session_or_persist_activity(guest_db):
    before = counts(guest_db)
    client = TestClient(app)
    for path in ('/guest', '/guest/login', '/guest?c001=true&eligible=true'):
        response = client.get(path)
        assert response.status_code == 200
        assert 'set-cookie' not in response.headers
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['referrer-policy'] == 'no-referrer'
        assert 'WC-GUEST-' not in response.text
        assert 'account-state-bootstrap' not in response.text
        assert 'account-create-form' not in response.text
        assert 'C001 registration starts only from the official C001 entry link' in response.text
    assert "connect-src 'none'" in client.get('/guest').headers['content-security-policy']
    assert 'account-create-form' in client.get('/setup?age=adult').text  # Ordinary entry is retained.
    assert counts(guest_db) == before


def test_signed_in_guest_url_requires_explicit_logout_and_does_not_enroll(guest_db):
    client = TestClient(app)
    assert client.post('/account/login', data={'woodchuck_id':'WC-GUEST-A','pin':'2468'}).status_code == 200
    before = counts(guest_db)
    old_state = client.get('/account/state').json()
    response = client.get('/guest')
    assert 'guest-confirm-logout' in response.text
    assert 'id="guest-setup-form"' not in response.text
    assert 'account-state-bootstrap' not in response.text
    assert client.get('/account/state').json() == old_state
    assert counts(guest_db) == before
    assert client.post('/account/logout').json() == {'authenticated': False}
    assert 'id="guest-setup-form"' in client.get('/guest').text
    assert client.get('/account/state').status_code == 401
    after = counts(guest_db)
    assert after.pop('revoked_browser_sessions') == before.pop('revoked_browser_sessions') + 1
    assert after == before  # Only authenticated-session retirement may persist.


@pytest.mark.parametrize('signed_in', [False, True])
def test_guest_confirmation_copy_describes_browser_session_with_or_without_student(guest_db, signed_in):
    client = TestClient(app)
    if signed_in:
        client.post('/account/login', data={'woodchuck_id': 'WC-GUEST-A', 'pin': '2468'})
    # Family pages establish CSRF session state even without a student account.
    family = client.get('/family/notice')
    assert 'Open Guest tools (may ask you to clear this browser session)' in family.text
    response = client.get('/guest')
    assert 'Woodshed session data is active in this browser and must be cleared before using Guest tools.' in response.text
    assert 'Opening this page has not cleared your browser session or joined C001.' in response.text
    assert '>Clear browser session and explore as Guest</button>' in response.text
    assert '>Keep this browser session</a>' in response.text
    assert 'A signed-in session is active' not in response.text


SUBMISSIONS = [
    ('post', '/practice-charts', {'practice_date':str(date.today()), 'minutes':10, 'note':'synthetic'}),
    ('post', '/practice-charts/pristine', {'detected_playing_seconds':60, 'submission_key':'synthetic-guest'}),
    ('post', '/arcade/plays', {'game_key':'blue'}),
    ('post', '/arcade/plays/fake-token/complete', {'score':100}),
    ('post', '/teams', {'name':'Synthetic Guests', 'emblem_key':'emoji:bear'}),
    ('post', '/teams/selection', {'team_id':1}),
    ('post', '/contests/camp-points/awards', {'activity_type':'care', 'activity_date':str(date.today())}),
    ('post', '/account/daily-secret', {'passcode':'union'}),
    ('put', '/account/state', {'account':{'woodchuckId':'WC-GUEST-A','serverRevision':7}, 'progress':{'credits':99999}}),
]


@pytest.mark.parametrize('method,path,payload', SUBMISSIONS)
@pytest.mark.parametrize('cookie_account', [None, 'B'])
def test_guest_flags_or_stale_page_identity_cannot_authorize_writes(guest_db, method, path, payload, cookie_account):
    client = TestClient(app)
    if cookie_account:
        assert client.post('/account/login', data={'woodchuck_id':'WC-GUEST-B','pin':'2468'}).status_code == 200
    before = counts(guest_db)
    with guest_db() as session:
        states = {s.profile_id:s.state_json for s in session.scalars(select(WoodchuckState))}
    response = client.request(method, path, json=payload,
        headers={'X-Woodshed-Account':'WC-GUEST-A','X-Guest':'false','X-C001':'true'})
    assert response.status_code == 401, response.text
    assert counts(guest_db) == before
    with guest_db() as session:
        assert {s.profile_id:s.state_json for s in session.scalars(select(WoodchuckState))} == states


def test_matching_header_preserves_authenticated_self_access_and_no_header_cannot_fake_login(guest_db):
    client = TestClient(app)
    assert client.get('/account/state', headers={'X-Woodshed-Account':'WC-GUEST-A'}).status_code == 401
    client.post('/account/login', data={'woodchuck_id':'WC-GUEST-A','pin':'2468'})
    own = client.get('/account/state', headers={'X-Woodshed-Account':'WC-GUEST-A'})
    assert own.status_code == 200
    assert own.headers['cache-control'] == 'no-store'
    assert own.json()['state']['practiceLog'] == [{'note':'saved account A'}]
    assert client.get('/account/state', headers={'X-Woodshed-Account':'-'}).status_code == 401
    assert client.get('/account/state').status_code == 200  # Ordinary API session contract.


def test_page_generation_rotates_on_same_account_login_and_is_not_authorization(guest_db):
    client = TestClient(app)
    def login():
        assert client.post('/account/login', data={'woodchuck_id':'WC-GUEST-A','pin':'2468'}).status_code == 200
    login()
    first = client.get('/account/me')
    assert first.headers['cache-control'] == 'no-store'
    generation = first.json()['page_generation']
    assert generation
    for path in ('/home', '/account/privacy'):
        page = client.get(path)
        assert page.headers['cache-control'] == 'no-store'
        assert f'data-page-generation="{generation}"' in page.text
        assert 'data-account-id="WC-GUEST-A"' in page.text
        assert '<div class="app-shell" hidden>' in page.text
        assert '<template id="account-scripts">' in page.text
    before = counts(guest_db)
    for _ in range(2):
        assert client.get('/account/me').json()['page_generation'] == generation
    assert counts(guest_db) == before
    assert client.post('/account/logout').status_code == 200
    anonymous = client.get('/account/me', headers={'X-Woodshed-Account':'WC-GUEST-A', 'X-Page-Generation':generation})
    assert anonymous.json() == {'authenticated':False,'profile':None}
    assert anonymous.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in anonymous.headers
    login()
    assert client.get('/account/me').json()['page_generation'] != generation


def test_legacy_account_session_gets_page_generation_only_when_rendered(guest_db):
    from app.account_routes import SESSION_PROFILE_ID, SESSION_PROFILE_VERSION
    from app.main import SESSION_SECRET
    from base64 import b64encode
    from itsdangerous import TimestampSigner
    import json
    with guest_db() as session:
        profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.woodchuck_id=='WC-GUEST-A'))
        data = {SESSION_PROFILE_ID:profile.id, SESSION_PROFILE_VERSION:profile.session_version}
    cookie = TimestampSigner(SESSION_SECRET).sign(b64encode(json.dumps(data).encode())).decode()
    client = TestClient(app)
    client.cookies.set('session',cookie)
    assert client.get('/account/me').json()['page_generation'] is None
    client.cookies.clear()
    client.cookies.set('session',cookie)
    page = client.get('/home')
    assert page.status_code == 200
    # Use the newly issued cookie, not the manually supplied domainless one.
    new_cookie = page.cookies.get('session')
    client.cookies.clear()
    client.cookies.set('session',new_cookie)
    generation = client.get('/account/me').json()['page_generation']
    assert generation
    assert f'data-page-generation="{generation}"' in page.text
