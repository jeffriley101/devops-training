"""Final artwork is the interface; semantic object targets supply its behavior."""
from html.parser import HTMLParser
from pathlib import Path
import struct
import re

from test_world_entry import client, login

ROOT = Path(__file__).resolve().parents[1]
SHED = {
    'L1': '/store', 'R1': 'shed-team-button',
    'L2': '/p-book', 'R2': 'instrument-object',
    'L3': 'xp-level-control', 'R3': 'metronome-open-button',
    'L4': 'shed-decorate-button', 'R4': 'tuner-open-button',
    'L5': 'mum-open-button', 'R5': 'sound-effects-button',
}
SHOP = {
    'L1': '/home', 'R1': 'gear', 'L2': 'crown', 'R2': 'little-buddy',
    'L3': 'goat', 'R3': '/quest', 'L4': 'contact',
    'R4': 'dandelion-object', 'L5': 'practice-room', 'R5': '/membership?as_account=student',
}


class Elements(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def cells(markup):
    return [(tag, a) for tag, a in Elements(markup).elements if 'data-scene-cell' in a]


def test_rendered_rooms_have_exact_semantic_cells_and_only_shed_editor(client):
    login(client)
    for path, mapping in [('/home', SHED), ('/store', SHOP)]:
        markup = client.get(path).text
        controls = cells(markup)
        assert len(controls) == 10
        assert [a['data-scene-cell'] for _, a in controls] == list(mapping)
        for tag, a in controls:
            assert tag in ('button', 'a')
            assert a['aria-label']
            assert 'tabindex' not in a
            assert (a.get('id') or a.get('data-shop-panel') or a.get('href')) == mapping[a['data-scene-cell']]
            if tag == 'button':
                assert a['type'] == 'button'
        assert markup.count('id="your-woodchuck"') == (1 if path == '/home' else 0)
        assert 'data-presentation-only' in markup
        assert 'data-student-woodchuck' in markup
        assert 'woodchuck-appearance-data' in markup
        assert 'scene-hotspots.css' in markup
        assert 'shop-object-column' not in markup
        assert 'woodshed-object-column' not in markup
    home = client.get('/home').text
    elements = Elements(home).elements
    secret = next(a for _, a in elements if a.get('id') == 'shed-secret-button')
    assert 'data-scene-cell' not in secret
    assert secret['aria-controls'] == 'shed-secret-panel'
    assert next(a for _, a in cells(home) if a['data-scene-cell'] == 'R2')['aria-controls'] == 'your-woodchuck'


def test_individual_uncropped_scene_contract_and_invisible_controls():
    css = (ROOT / 'static/css/scene-hotspots.css').read_text()
    assert 'grid-template-columns: repeat(2, 50%)' not in css
    assert 'grid-template-rows: repeat(5, 20%)' not in css
    for rule in ['left: var(--hotspot-x)', 'top: var(--hotspot-y)',
                 'min-width: 44px', 'min-height: 44px', 'aspect-ratio: var(--art-width) / var(--art-height)',
                 'object-fit: contain', '100dvh - var(--room-nav-height)', 'safe-area-inset-bottom',
                 ':focus-visible', 'outline: 3px solid #fff', 'pointer-events: none']:
        assert rule in css
    for filename, grid in [('home.html', 'woodshed-hotspots'), ('store.html', 'shop-hotspots')]:
        template = (ROOT / 'templates' / filename).read_text()
        hotspots = template.split(f'id="{grid}"')[1].split('</div>')[0]
        assert not any(0x1F000 <= ord(c) <= 0x1FFFF for c in hotspots)
    motion = (ROOT / 'static/js/woodchuck-motion.js').read_text()
    assert 'data-presentation-only' in motion
    assert 'if (!presentationOnly)' in motion


def test_approved_art_dimensions_and_no_deleted_production_references():
    sizes = {'arcade/arcade-entry-splash.png': (512, 1024), 'sax-viking-portrait.png': (512, 1024),
             'shed-cabin-new.png': (948, 1659), 'shop-viking-valley-fair.png': (1024, 1536),
             'rhythm-baseball-cabinet.png': (1024, 1536)}
    for filename, size in sizes.items():
        data = (ROOT / 'static/img' / filename).read_bytes()
        assert data[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', data[16:24]) == size
    deleted = ['landing-painting.png', 'shop2.png', 'woodchuck-drum.png', 'woodchuck-home.png']
    for folder in ['app', 'templates', 'static/css', 'static/js']:
        for source in (ROOT / folder).rglob('*'):
            if source.suffix in ('.py', '.html', '.css', '.js'):
                text = source.read_text()
                assert not any(name in text for name in deleted), source


def test_baseball_is_honest_arcade_feature_and_four_practice_rooms_remain(client):
    markup = client.get('/arcade').text
    feature = markup.split('class="rhythm-baseball-feature"')[1].split('</section>\n</section>')[0]
    assert feature.index('TOP 5') < feature.index('rhythm-baseball-cabinet.png')
    assert 'Coming soon. Standings will appear when Rhythm Baseball launches.' in feature
    assert 'width="1024" height="1536"' in feature
    assert '<a ' not in feature
    assert '<button' not in feature
    assert 'data-arcade-art' not in feature
    assert 'Gameplay is not available yet.' in feature
    css = (ROOT / 'static/css/arcade-art.css').read_text()
    wallpaper = css.split('.arcade-lobby {')[1].split('}')[0]
    assert 'repeating-linear-gradient(' in wallpaper
    assert 'url(' not in wallpaper
    assert 'animation' not in wallpaper
    assert 'background-repeat: no-repeat' in wallpaper
    assert 'background-size: auto' in wallpaper
    assert 'aspect-ratio: 2 / 3' in css
    assert 'object-fit: contain' in css
    shop = client.get('/store').text
    assert shop.count('class="practice-room-emoji-control practice-room-door"') == 4
    assert 'Room E' not in shop
    assert 'Spectrogram. Temporarily unavailable' in shop
    assert 'brassspectrogram.netlify.app' not in shop
    assert 'rhythm-baseball' not in shop


def test_hotspot_rectangles_keep_twenty_actions_clear_of_each_other_and_character():
    css = (ROOT / 'static/css/scene-hotspots.css').read_text()
    for room, size, mapping in [('woodshed', (948, 1659), SHED), ('shop', (1024, 1536), SHOP)]:
        rules = re.findall(r'#' + room + r'-hotspots > \[data-scene-cell="([LR][1-5])"\] \{([^}]+)\}', css)
        assert [cell for cell, _ in rules] == list(mapping)
        for vw, vh in [(1440, 900), (390, 844), (320, 568), (844, 390), (568, 320)]:
            width = min(vw, vh * size[0] / size[1])
            height = width * size[1] / size[0]
            rectangles = []
            for cell, body in rules:
                values = [float(re.search(r'--hotspot-' + field + r': ([0-9.]+)%', body)[1]) for field in ('x', 'y', 'width', 'height')]
                x, y = width * values[0] / 100, height * values[1] / 100
                w, h = max(44, width * values[2] / 100), max(44, height * values[3] / 100)
                assert x >= 0 and y >= 0 and x + w <= width + .01 and y + h <= height + .01, (room, cell, vw, vh)
                rect = (x, y, x + w, y + h)
                character = (width * .32, height * .50, width * .68, height * .96)

                def intersects(a, b):
                    return min(a[2], b[2]) - max(a[0], b[0]) > .01 and min(a[3], b[3]) - max(a[1], b[1]) > .01

                assert not intersects(rect, character), (room, cell, 'character', vw, vh)
                assert not any(intersects(rect, prior) for prior in rectangles), (room, cell, vw, vh)
                rectangles.append(rect)
