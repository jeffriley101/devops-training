"""Presentation/navigation regression checks; no production services."""
import re
from html import unescape
from pathlib import Path

from fastapi.testclient import TestClient
from app import main
from app.arcade_access import CLASSROOM, classroom_authorized

ROOT = Path(__file__).resolve().parents[1]


def test_cabinets_have_decorative_decks_and_standings_outside_links():
    html = TestClient(main.app).get("/arcade").text
    cabinets = re.findall(r'<article class="arcade-cabinet .*?</article>', html, re.S)
    assert len(cabinets) == 9
    assert 'arcade-cabinet-marquee' not in html
    for cabinet in cabinets:
        link = re.search(r'<a class="arcade-cabinet-link".*?</a>', cabinet, re.S).group()
        assert 'arcade-leaderboard' not in link
        assert 'aria-label="Play ' in link
        assert 'class="arcade-art-fallback"' in link
        if 'arcade-cabinet-history' not in cabinet:
            assert cabinet.index('arcade-leaderboard') < cabinet.index('arcade-cabinet-link')
        deck = link[link.index('<span class="arcade-cabinet-control-panel"'):link.index('<span class="arcade-cabinet-best"')]
        assert 'aria-hidden="true"' in deck
        assert deck.count('class="arcade-joystick"') == 1
        assert deck.count('class="arcade-action-button"') == 2
        assert 'class="arcade-token-slot"' in deck and 'Dandelions' in deck
        assert not any(token in deck for token in ('<button', '<a ', 'tabindex', 'onclick'))
    assert html.count('>Always free</span>') == 2
    assert html.count('>100 Dandelions · 3 attempts</span>') == 7
    # Existing three explicitly cropped legacy visuals survive; no generic injection.
    assert html.count('class="arcade-keeper-art"') == 3


def test_practice_doors_and_locked_exercises():
    # Capture navigation template without needing a database-backed store context.
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(ROOT / "templates"))
    store = env.get_template("store.html").render()
    doors = re.findall(r'<a class="practice-room-emoji-control practice-room-door".*?</a>', store)
    assert len(doors) == 4
    for door, letter, label in zip(doors, "ABCD", [
        "Skill Building Exercises", "Pristine P-Chart", "Arcade Room", "Spectrogram"
    ]):
        assert f'door-tag">{letter}</span>' in door
        assert f'<strong>{label}</strong>' in door
    assert 'href="/practice/skill-building"' in doors[0]
    assert 'href="/practice/pristine"' in doors[1]
    assert 'href="/arcade"' in doors[2]
    assert 'href="https://brassspectrogram.netlify.app/"' in doors[3]
    assert 'target="_blank"' in doors[3] and 'rel="noopener noreferrer"' in doors[3]
    client = TestClient(main.app)
    response = client.get("/practice/skill-building")
    assert response.status_code == 200
    page = unescape(response.text)
    lobby = client.get("/arcade").text
    assert '<h1>Skill Building Exercises</h1>' in page
    assert 'Personal Full Access does not unlock' in page
    assert 'Classes' in page
    assert '/static/js/arcade-art.js?v=4' in page
    for key, label in CLASSROOM.items():
        assert f'<strong>{label}</strong> · Locked' in page
        assert f'data-arcade-art="{key}"' not in lobby
        assert label not in lobby
        assert f'href="/arcade/{key}"' not in page
    assert classroom_authorized(None, None) is False
