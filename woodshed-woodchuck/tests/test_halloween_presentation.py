"""Canonical presentation and read-only page rendering on synthetic SQLite."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import main
from app.board_seasons import board_season_presentation
from app.db import Base
from app.models import Season
from app.seasons import CANONICAL_SEASONS, bootstrap_canonical_seasons
from test_guest_boundary import guest_db


def set_clock(monkeypatch, instant):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)
    monkeypatch.setattr(main, "datetime", Clock)


def snapshot(factory):
    with factory() as session:
        return {table.name: sorted(repr(tuple(row)) for row in session.execute(select(table)))
                for table in Base.metadata.sorted_tables}


@pytest.fixture
def seasonal_client(guest_db, monkeypatch):
    with guest_db() as session:
        bootstrap_canonical_seasons(session)
        session.commit()
    set_clock(monkeypatch, datetime(2026, 9, 29, 18, tzinfo=timezone.utc))
    with TestClient(main.app) as client:
        assert client.post("/account/login", data={"woodchuck_id": "WC-GUEST-A", "pin": "2468"}).status_code == 200
        yield client, guest_db


def test_halloween_maps_to_harvest_without_renaming_the_season():
    presentation = board_season_presentation(Season(key="halloween-2026", name="Halloween"))
    assert presentation.artwork_theme == "harvest"
    assert presentation.title == "Halloween"
    assert presentation.polaroid_url == "/static/img/seasonal/harvest/board_polaroid_1200x900.jpg"
    assert presentation.master_url == "/static/img/seasonal/harvest/master_2x1.png"


@pytest.mark.parametrize("key", [s.key for s in CANONICAL_SEASONS if s.key != "halloween-2026"] + ["halloween-2027", "unknown"])
def test_artwork_mapping_requires_the_exact_canonical_key(key):
    presentation = board_season_presentation(Season(key=key, name="Halloween"))
    assert presentation.artwork_theme is None
    assert presentation.polaroid_url is None
    assert presentation.master_url is None


def test_board_polaroid_and_shed_overlay(seasonal_client):
    client, _ = seasonal_client
    board = client.get("/quest")
    assert board.status_code == 200
    assert "back-to-school-streamers" not in board.text
    assert 'data-season-theme="harvest"' in board.text
    assert 'halloween-decor--board' in board.text
    assert 'data="/static/img/seasonal/harvest/board_polaroid_1200x900.jpg"' in board.text
    assert 'href="/static/img/seasonal/harvest/master_2x1.png"' in board.text
    assert 'aria-labelledby="season-art-title"' in board.text
    assert 'method="dialog"' in board.text
    assert "Halloween Standings" in board.text
    home = client.get("/home")
    assert home.status_code == 200
    assert 'halloween-decor--shed' in home.text
    assert 'data-presentation-only aria-hidden="true"' in home.text
    assert 'id="shed-secret-button"' in home.text
    assert 'id="shed-decoration-layer"' in home.text
    assert 'src="/static/img/shed-cabin-new.png"' in home.text


@pytest.mark.parametrize("instant, halloween", [
    (datetime(2026, 9, 28, 4, 59, tzinfo=timezone.utc), False),
    (datetime(2026, 9, 28, 5, tzinfo=timezone.utc), True),
    (datetime(2026, 11, 2, 5, 59, tzinfo=timezone.utc), True),
    (datetime(2026, 11, 2, 6, tzinfo=timezone.utc), False),
])
def test_pages_follow_canonical_chicago_boundary(seasonal_client, monkeypatch, instant, halloween):
    client, _ = seasonal_client
    set_clock(monkeypatch, instant)
    board, home = client.get("/quest").text, client.get("/home").text
    assert ("halloween-decor--board" in board) is halloween
    assert ("halloween-decor--shed" in home) is halloween
    assert ("season-polaroid-link" in board) is halloween
    assert ("back-to-school-streamers" in board) is not halloween
    for html in (board, home):
        assert ("seasonal-presentation.css" in html) is halloween


@pytest.mark.parametrize("calendar_present", [False, True])
def test_rendering_does_not_write_or_bootstrap_any_rows(guest_db, monkeypatch, calendar_present):
    if calendar_present:
        with guest_db() as session:
            bootstrap_canonical_seasons(session)
            session.commit()
    set_clock(monkeypatch, datetime(2026, 9, 29, 18, tzinfo=timezone.utc))
    client = TestClient(main.app)
    assert client.post("/account/login", data={"woodchuck_id": "WC-GUEST-A", "pin": "2468"}).status_code == 200
    before = snapshot(guest_db)
    engine = guest_db.kw["bind"]
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA query_only=ON")
    event.listen(engine, "before_cursor_execute", observe)
    try:
        for path in ("/quest", "/home"):
            response = client.get(path)
            assert response.status_code == 200
            assert ("halloween-decor" in response.text) is calendar_present
        # Guest has its own tools template and no seasonal room/inventory layer.
        guest = TestClient(main.app).get("/guest")
        assert guest.status_code == 200
        assert 'id="guest-setup-form"' in guest.text
        assert 'id="shed-secret-button"' in guest.text
        assert "halloween-decor" not in guest.text
        assert snapshot(guest_db) == before
        assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "REPLACE"}
        # Login can grant its existing daily reward before this snapshot. Every
        # reward row is included in the full before/after comparison above.
        for table in ("owned_item_copies", "contests", "contest_weeks"):
            assert before[table] == []
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only=OFF")
