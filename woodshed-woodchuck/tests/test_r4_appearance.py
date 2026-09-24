"""Appearance is an account preference, never a client entitlement grant."""
import json
import re

import pytest
from app import appearance, account_routes
from app.models import WoodchuckState, WoodchuckProfile
from app.instruments import INSTRUMENT_OPTIONS, shed_character_url
from test_world_entry import client, login


def get_bootstrap(response):
    return json.loads(re.search(r'<script id="woodchuck-appearance-data" type="application/json">(.*?)</script>', response.text, re.S)[1])


def test_default_palette_and_shared_mapping(client):
    login(client)
    a = client.get('/account/appearance').json()
    assert [c['label'] for c in a['palette'] if not c['premium']] == ['Blue', 'Green', 'Red', 'Purple', 'Pink']
    assert a['saved'] == a['effective'] == {'hoodie': 'blue', 'hat': 'green'}
    for path in ['/home', '/store']:
        response = client.get(path)
        assert get_bootstrap(response)['source'] == '/static/img/woodchuck-flute.png'
        assert 'data-student-woodchuck' in response.text
        assert 'Your Woodchuck' in response.text
    room = client.get('/store').text
    assert 'Spectrogram. Temporarily unavailable' in room
    assert 'brassspectrogram.netlify.app' not in room


@pytest.mark.parametrize('color', [c['key'] for c in appearance.PALETTE if c['premium']])
def test_premium_colors_rejected_without_entitlement(client, color):
    login(client)
    result = client.patch('/account/appearance', json={'instrument': 'Trumpet', 'hoodie': color, 'hat': 'green'})
    assert result.status_code == 403
    assert 'Premium' in result.json()['detail']
    assert client.get('/account/appearance').json()['instrument'] == 'Flute'


def test_free_colors_and_instrument_persist_across_sessions(client):
    login(client)
    for color in ['blue', 'green', 'red', 'purple', 'pink']:
        response = client.patch('/account/appearance', json={'instrument': 'Trumpet', 'hoodie': color, 'hat': color})
        assert response.status_code == 200
    client.post('/account/logout')
    login(client)
    data = client.get('/account/state').json()
    assert data['state']['appearance'] == {'hoodie': 'pink', 'hat': 'pink'}
    for path in ['/home', '/store']:
        assert get_bootstrap(client.get(path))['source'] == '/static/img/woodchuck-trumpet.png'


def test_expiry_retains_preferences_and_restores_them(client, monkeypatch):
    login(client)
    entitled = [True]
    monkeypatch.setattr(appearance, 'student_has_full_access', lambda *_: entitled[0])
    result = client.patch('/account/appearance', json={'instrument':'Trumpet', 'hoodie':'orange', 'hat':'gold'})
    assert result.status_code == 200
    entitled[0] = False
    data = client.get('/account/appearance').json()
    assert data['effective'] == {'hoodie':'blue', 'hat':'green'}
    assert data['saved'] == {'hoodie':'orange', 'hat':'gold'}
    # Keeping an expired selection must not gate instrument choice.
    assert client.patch('/account/appearance', json={'instrument':'Tuba', **data['saved']}).status_code == 200
    assert client.patch('/account/appearance', json={'instrument':'Tuba', 'hoodie':'navy', 'hat':'gold'}).status_code == 403
    entitled[0] = True
    assert client.get('/account/appearance').json()['effective'] == data['saved']


def test_generic_state_cannot_bypass_entitlement_or_erase_preference(client):
    login(client)
    client.patch('/account/appearance', json={'instrument':'Flute', 'hoodie':'purple', 'hat':'pink'})
    snapshot = client.get('/account/state').json()
    state = snapshot['state']
    state['account'] = {'serverRevision': snapshot['revision']}
    state['appearance'] = {'hoodie':'orange', 'hat':'gold'}
    assert client.put('/account/state', json=state).status_code == 200
    assert client.get('/account/appearance').json()['saved'] == {'hoodie':'purple', 'hat':'pink'}


def test_invalid_color_is_atomic_and_anonymous_rejected(client):
    assert client.patch('/account/appearance', json={'instrument':'Flute','hoodie':'blue','hat':'green'}).status_code == 401
    login(client)
    assert client.patch('/account/appearance', json={'instrument':'Tuba','hoodie':'rainbow','hat':'green'}).status_code == 400
    assert client.get('/account/appearance').json()['instrument'] == 'Flute'


def test_every_supported_instrument_is_free_and_shared_between_rooms(client):
    login(client)
    for instrument in INSTRUMENT_OPTIONS:
        result = client.patch('/account/appearance', json={'instrument': instrument, 'hoodie': 'red', 'hat': 'blue'})
        assert result.status_code == 200
        assert result.json()['premium'] is False
        assert result.json()['source'] == shed_character_url(instrument)
        assert result.json()['revision'] == result.json()['state_revision']
        for path in ['/home', '/store']:
            assert get_bootstrap(client.get(path))['source'] == shed_character_url(instrument)


def test_malformed_legacy_colors_fall_back_safely(client):
    login(client)
    with account_routes.SessionLocal() as session:
        state = session.get(WoodchuckState, 1)
        state.state_json = {**state.state_json, 'appearance': {'hoodie': [], 'hat': None}}
        session.commit()
    assert client.get('/account/appearance').json()['effective'] == appearance.DEFAULTS


def test_real_membership_capability_controls_appearance(client):
    from datetime import timedelta
    from app import memberships
    from app.models import Membership
    login(client)
    with account_routes.SessionLocal() as session:
        membership = memberships.create_complimentary_membership(
            session, memberships.Actor('student', 1), memberships.Actor('admin'),
            at=memberships.clock() - timedelta(days=1))
        session.commit()
        membership_id = membership.id
    result = client.patch('/account/appearance', json={'instrument': 'Trumpet', 'hoodie': 'orange', 'hat': 'gold'})
    assert result.status_code == 200
    assert result.json()['premium'] is True
    with account_routes.SessionLocal() as session:
        membership = session.get(Membership, membership_id)
        membership.access_until = memberships.clock() - timedelta(seconds=1)
        session.commit()
    payload = client.get('/account/appearance').json()
    assert payload['saved'] == {'hoodie': 'orange', 'hat': 'gold'}
    assert payload['effective'] == appearance.DEFAULTS
