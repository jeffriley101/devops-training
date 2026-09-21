"""Server-side cookie replay regression using real routes and signed cookies."""

from datetime import datetime, timedelta, timezone
import sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import OperationalError
from app.main import app
from app.db import Base
from app.models import WoodchuckProfile, WoodchuckState
from app.session_revocations import RevokedBrowserSession
from app import session_revocations as service
from app.security import hash_pin


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as s:
        for label in ("A", "B"):
            p = WoodchuckProfile(
                woodchuck_id="WC-REVOKE-" + label,
                display_name="Synthetic " + label,
                pin_hash=hash_pin("2468"),
                instrument="Flute",
                level="Beginner",
                goal="Practice",
            )
            s.add(p)
            s.flush()
            s.add(WoodchuckState(profile_id=p.id, state_json={}, revision=0))
            # These logout regressions require an eligible synthetic account.
            from app.age_privacy import declare_age
            declare_age(s, p.id, "13to17")
        s.commit()
    yield factory
    engine.dispose()


def login(client, label="A"):
    r = client.post(
        "/account/login", data={"woodchuck_id": "WC-REVOKE-" + label, "pin": "2468"}
    )
    assert r.status_code == 200
    return client.cookies.get("session")


def replay(cookie):
    client = TestClient(app)
    client.cookies.set("session", cookie)
    return client


def test_old_cookie_cannot_restore_authentication_after_logout(db):
    client = TestClient(app)
    old = login(client)
    data = client.get("/account/me")
    assert data.status_code == 200 and "set-cookie" not in data.headers
    assert client.post("/account/logout").json() == {"authenticated": False}
    delayed = replay(old)
    assert delayed.get("/account/me").json()["authenticated"] is False
    assert delayed.put("/account/state", json={}).status_code == 401
    assert 'id="guest-setup-form"' in delayed.get("/guest").text
    with db() as s:
        assert s.scalar(select(func.count()).select_from(RevokedBrowserSession)) == 1


def test_logout_does_not_end_an_independent_browser_or_fresh_login(db):
    a = TestClient(app)
    b = TestClient(app)
    stale = login(a)
    login(b)
    assert a.post("/account/logout").status_code == 200
    assert b.get("/account/state").status_code == 200
    fresh = login(a)
    assert fresh != stale and a.get("/account/state").status_code == 200
    retired = replay(stale)
    rejected = retired.get("/account/me")
    assert rejected.json()["authenticated"] is False
    assert (
        "set-cookie" not in rejected.headers
    )  # A stale response cannot erase the fresh cookie.


def test_account_switch_retires_old_browser_cookie(db):
    c = TestClient(app)
    old = login(c, "A")
    login(c, "B")
    assert replay(old).get("/account/state").status_code == 401
    assert c.get("/account/me").json()["profile"]["woodchuck_id"] == "WC-REVOKE-B"


def test_revocation_is_not_a_guest_profile_or_scan_record(db):
    c = TestClient(app)
    for _ in range(3):
        assert "set-cookie" not in c.get("/guest").headers
        assert c.post("/account/logout").status_code == 200
    with db() as s:
        assert s.scalar(select(func.count()).select_from(RevokedBrowserSession)) == 0


def test_logout_failure_is_not_acknowledged_and_saved_account_survives(db, monkeypatch):
    c = TestClient(app)
    cookie = login(c)

    def outage(*args):
        raise OperationalError("synthetic", {}, Exception("unavailable"))

    monkeypatch.setattr(service, "retire", outage)
    assert c.post("/account/logout").status_code == 503
    assert c.get("/account/state").status_code == 200
    assert replay(cookie).get("/account/state").status_code == 200


def test_revocation_lookup_outage_fails_closed(db, monkeypatch):
    c = TestClient(app)
    login(c)

    def outage(*args):
        raise OperationalError("synthetic", {}, Exception("unavailable"))

    monkeypatch.setattr(service, "revoked", outage)
    assert c.get("/account/state").status_code == 503
    assert c.put("/account/state", json={}).status_code == 503
    assert TestClient(app).get("/guest").status_code == 200


def test_adult_admin_cookie_is_also_retired(db, monkeypatch):
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "synthetic-admin-only")
    c = TestClient(app)
    import re

    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/admin/login").text).group(
        1
    )
    assert (
        c.post(
            "/admin/login",
            data={"csrf": token, "token": "synthetic-admin-only"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    old = c.cookies.get("session")
    assert c.get("/admin/security/rate-limit").status_code == 200
    assert c.post("/account/logout").status_code == 200
    assert replay(old).get("/admin/security/rate-limit").status_code == 403


def test_retention_purge_only_removes_expired_revocations(db):
    with db() as s:
        now = datetime.now(timezone.utc)
        for key, days in [("a", -1), ("b", 1)]:
            s.add(
                RevokedBrowserSession(
                    token_hash=key * 64,
                    revoked_at=now - timedelta(days=20),
                    expires_at=now + timedelta(days=days),
                )
            )
        s.commit()
        service.purge_expired(s, now=now)
        s.commit()
        assert list(s.scalars(select(RevokedBrowserSession.token_hash))) == ["b" * 64]


def test_legacy_cookie_without_nonce_is_retired_even_if_captured_before_deploy(db):
    from base64 import b64encode
    from itsdangerous import TimestampSigner
    from app.main import SESSION_SECRET
    import json

    cookie = (
        TimestampSigner(SESSION_SECRET)
        .sign(
            b64encode(
                json.dumps(
                    {"woodchuck_profile_id": 1, "woodchuck_session_version": 0}
                ).encode()
            )
        )
        .decode()
    )
    c = replay(cookie)
    assert c.get("/account/state").status_code == 200
    # Use the exact untouched pre-upgrade cookie to sign out, then replay it.
    c = replay(cookie)
    assert c.post("/account/logout").status_code == 200
    stale = replay(cookie)
    assert stale.get("/account/state").status_code == 401
    assert "guest-confirm-logout" not in stale.get("/guest").text


def test_absolute_expiry_stays_invalid_after_revocation_retention(db, monkeypatch):
    c = TestClient(app)
    old = login(c)
    assert c.post("/account/logout").status_code == 200
    with db() as s:
        service.purge_expired(s, now=datetime.now(timezone.utc) + timedelta(days=16))
        s.commit()
    original_time = service.time.time
    monkeypatch.setattr(service.time, "time", lambda: original_time() + 16 * 86400)
    assert replay(old).get("/account/state").status_code == 401
