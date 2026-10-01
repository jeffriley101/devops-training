"""Opt-in loopback disposable PostgreSQL, using H1B's real lock race harness."""
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import models as m, teams, team_continuity_repair as repair
from app.contests import finalize_contest_week
from tests import test_season_team_activation as spec
from tests.test_team_families import disposable_url
from tests.test_team_continuity_postgres import ordered_race
from tests.team_factory import make_team


@pytest.fixture
def pg(tmp_path):
    url = disposable_url(tmp_path, "postgresql")
    engine = spec.seed(url)
    yield url, engine
    engine.dispose()


def test_preflight_database_readonly(pg, monkeypatch):
    original = repair.inventory.readonly_connection
    checked_count = []
    @contextmanager
    def checked(url):
        with original(url) as c:
            assert c.scalar(text("SHOW transaction_read_only")) == "on"
            assert c.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            checked_count.append(True)
            yield c
    monkeypatch.setattr(repair.inventory, "readonly_connection", checked)
    before = spec.base.full_snapshot(pg[1])
    assert spec.job.preflight(pg[0], now=spec.BOUNDARY - timedelta(hours=1))["status"] == "READY"
    assert len(checked_count) == 1 and spec.base.full_snapshot(pg[1]) == before
    with original(pg[0]) as c:
        with pytest.raises(DBAPIError) as exc:
            c.execute(text("UPDATE teams SET display_name='forbidden'"))
        assert exc.value.orig.sqlstate == "25006"


def test_retired_activation_preserves_postgres_history(pg):
    spec.test_seasonal_activation_is_retired_without_copying(pg, spec.BOUNDARY)
