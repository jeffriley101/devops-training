"""Human smoke corrections: viewport rooms, bootstrap appearance, current choices."""
from pathlib import Path
import re
import pytest
from app import main
from app.models import WoodchuckProfile
from app.instruments import INSTRUMENT_OPTIONS
from test_world_entry import client, login
from test_r4a_artwork import Elements
from test_r4_appearance import get_bootstrap

ROOT = Path(__file__).resolve().parents[1]


def test_rooms_have_no_streak_strip_and_omit_only_room_footer(client):
    login(client)
    for path in ['/home', '/store']:
        markup = client.get(path).text
        assert markup.count('id="login-streak-card"') == (1 if path == '/home' else 0)
        assert '<main' in markup
        assert '_room_streak' not in markup
        if path == '/home':
            assert markup.index('id="xp-panel"') < markup.index('id="login-streak-card"') < markup.index('id="shed-team-panel"')
        assert 'href="/family/practice"' not in markup
        assert 'href="/family/parent-access"' not in markup
        assert 'data-presentation-only' in markup
    assert 'href="/family/practice"' in client.get('/p-book').text
    assert 'href="/family/parent-access"' in client.get('/p-book').text


def test_editor_offers_only_current_authoritative_catalog(client):
    login(client)
    markup = client.get('/home').text
    select = re.search(r'<select id="woodchuck-instrument".*?</select>', markup, re.S)[0]
    values = [a['value'] for tag,a in Elements(select).elements if tag == 'option']
    assert values == INSTRUMENT_OPTIONS
    assert len(values) == 16


@pytest.mark.parametrize('legacy', ['Drum Major','Color Guard','Accordion','Harp','Auxiliary Percussion'])
def test_legacy_profiles_keep_instrument_when_saving_colors_but_cannot_be_new_choices(client, legacy):
    login(client)
    # A new legacy selection is rejected.
    assert client.patch('/account/appearance',json={'instrument':legacy,'hoodie':'red','hat':'pink'}).status_code == 400
    with main.SessionLocal() as session:
        session.get(WoodchuckProfile,1).instrument = legacy
        session.commit()
    for path in ['/home','/store']:
        assert get_bootstrap(client.get(path))['instrument'] == legacy
    saved=client.patch('/account/appearance',json={'instrument':legacy,'hoodie':'red','hat':'pink'})
    assert saved.status_code == 200
    assert saved.json()['instrument'] == legacy
    assert client.patch('/account/appearance',json={'instrument':'Flute','hoodie':'red','hat':'pink'}).status_code == 200


def test_character_readiness_and_palette_are_bootstrapped(client):
    login(client)
    client.patch('/account/appearance',json={'instrument':'Trumpet','hoodie':'purple','hat':'pink'})
    for path in ['/home','/store']:
        data=get_bootstrap(client.get(path))
        assert data['effective']=={'hoodie':'purple','hat':'pink'}
        pink=next(c for c in data['palette'] if c['key']=='pink')
        assert pink['saturation_floor']>=.6
        assert 0<pink['lightness_lift']<.3
    js=(ROOT/'static/js/woodchuck-appearance.js').read_text()
    assert 'await decoded.decode()' in js
    assert "image.setAttribute('data-appearance-ready'" in js
    assert "fetch('/account/appearance'" in js
    assert "method: 'PATCH'" in js
    assert "localStorage" not in js and "sessionStorage" not in js


def test_room_css_and_static_exuberant_lasers():
    css=(ROOT/'static/css/scene-hotspots.css').read_text()
    assert 'height: 100dvh' in css and 'overflow: hidden' in css
    assert '--room-streak-height' not in css
    assert '--room-scene-height: calc(100dvh - var(--room-nav-height) - env(safe-area-inset-top))' in css
    assert '[data-student-woodchuck]:not([data-appearance-ready]) { visibility: hidden; }' in css
    panel=css.split('body.artwork-room-page [data-room-panel] {')[1].split('}')[0]
    assert 'position: fixed' in panel and 'overflow: auto' in panel
    laser=(ROOT/'static/css/arcade-art.css').read_text().split('.arcade-lobby {')[1].split('}')[0]
    assert laser.count('repeating-linear-gradient')>=5
    assert laser.count('radial-gradient')>=2
    assert 'background-repeat: no-repeat' in laser
    assert 'background-size: auto' in laser
    assert 'url(' not in laser and 'animation' not in laser
