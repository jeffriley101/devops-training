"""Entrance wiring and account scope, using only isolated in-memory SQLite."""
import hashlib
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, main, session_revocations
from app.db import Base
from app.models import WoodchuckProfile
from app.security import hash_pin

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(account_routes, 'SessionLocal', sessions)
    monkeypatch.setattr(session_revocations, 'SessionLocal', sessions)
    with sessions() as db:
        db.add(WoodchuckProfile(woodchuck_id='WC-ENTRY', display_name='Entry',
            pin_hash=hash_pin('1234'), instrument='Flute', level='Beginner', goal='Practice'))
        db.add(WoodchuckProfile(woodchuck_id='WC-OTHER', display_name='Other',
            pin_hash=hash_pin('1234'), instrument='Flute', level='Beginner', goal='Practice'))
        db.commit()
        from app.age_privacy import declare_age
        declare_age(db, 1, 'adult')
        declare_age(db, 2, 'adult')
        db.commit()
    with TestClient(main.app) as test_client:
        yield test_client
    engine.dispose()


def login(client, account='WC-ENTRY'):
    result = client.post('/account/login', data={'woodchuck_id':account, 'pin':'1234'})
    assert result.status_code == 200


def marker(response):
    return re.search(r'data-world-entry-account="([^"]*)"', response.text)[1]


def test_account_scope_stable_across_logins_and_distinct_for_other_students(client):
    assert marker(client.get('/')) == ''
    login(client)
    first = marker(client.get('/'))
    assert re.fullmatch('[0-9a-f]{64}', first)
    assert 'WC-ENTRY' not in first
    assert marker(client.get('/home')) == first
    failed = client.post('/account/login', data={'woodchuck_id':'WC-ENTRY', 'pin':'0000'})
    assert failed.status_code == 401
    assert marker(client.get('/')) == first
    assert client.post('/account/logout').status_code == 200
    assert marker(client.get('/')) == ''
    login(client)
    assert marker(client.get('/home')) == first
    client.post('/account/logout')
    login(client, 'WC-OTHER')
    assert marker(client.get('/home')) != first


def test_obsolete_login_marker_removed():
    for path in ['app/account_routes.py', 'app/main.py', 'templates/base.html']:
        assert 'world_entry_session' not in (ROOT / path).read_text()
    assert 'worldEntrySession' not in (ROOT / 'static/js/world-entry.js').read_text()


def test_exact_hooks_destinations_and_static_versions(client):
    login(client)
    welcome = client.get('/').text
    assert welcome.count('data-world-entry="woodshed"') == 1
    assert 'href="/home" data-requires-profile="true" data-world-entry="woodshed"' in welcome
    assert 'href="/setup"' in welcome and 'href="/login"' in welcome
    assert 'rel="preload" as="image" href="/static/img/woodshed-painting.png"' in welcome
    scripts = re.search(r'<template id="account-scripts">(.*?)</template>', welcome, re.S)[1]
    assert '/static/js/world-entry.js?v=3' in scripts
    assert 'data-page-generation="' in welcome and 'class="app-shell" hidden' in welcome
    assert 'href="/guest"' in welcome
    store = client.get('/store').text
    assert store.count('data-world-entry="arcade"') == 1
    assert re.search(r'<a [^>]*href="/arcade"[^>]*data-world-entry="arcade"', store)
    assert 'rel="preload" as="image" href="/static/img/arcade/arcade-entry-splash.png"' in store
    for url in ['/static/css/world-entry.css?v=2', '/static/js/world-entry.js?v=3']:
        assert url in welcome
        assert client.get(url).status_code == 200
    assert 'data-world-entry="arcade"' not in client.get('/arcade').text


def test_approved_asset_exact_bytes_and_no_alternate_runtime_name(client):
    response = client.get('/static/img/arcade/arcade-entry-splash.png')
    assert response.headers['content-type'] == 'image/png'
    assert len(response.content) == 3013191
    assert hashlib.sha256(response.content).hexdigest() == '4261c91f78e16c28dc36891b7f794adb4486633961edcc5d9a339f27f19f40bd'
    source = (ROOT / 'static/js/world-entry.js').read_text()
    assert re.findall(r'/static/img/arcade/[^"\s]+', source) == ['/static/img/arcade/arcade-entry-splash.png']


def test_real_javascript_controller():
    result = subprocess.run(['node', '--test', 'tests/test_world_entry.js'], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
