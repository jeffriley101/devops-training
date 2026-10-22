"""Game Mode authority on real migrated SQLite/PostgreSQL; no UI replacement."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
import sys
from threading import Event
import time

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import models as m
from app.age_models import AccountPrivacy
from app.age_privacy import declare_age
from app.db import Base
from app.game_modes import GAME_MODES, bootstrap_game_mode, mode_state, normalize_game_mode
from app.security import hash_pin
from tests.test_weekly2_foundation_migration import (
    db, config, revision, rows, profile, seed_history, schema_contract, NOW,
)

OLD, NEW = "w25weekly001", "w25weekly002"
PATH = "/account/profile/game-mode"


def without_mode(data):
    result = deepcopy(data)
    for row in result.get("woodchuck_profiles", []):
        row.pop("game_mode", None)
    return result


def test_linear_ancestry_and_catalog():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == ["w25weekly005"]
    assert script.get_revision("w25weekly005").down_revision == "w25weekly004"
    assert script.get_revision("w25weekly004").down_revision == "w25weekly003"
    assert script.get_revision("w25weekly003").down_revision == NEW
    assert script.get_revision(NEW).down_revision == OLD
    assert script.get_revision(OLD).down_revision == "c24class001"
    assert len(list(script.walk_revisions())) == 67
    assert GAME_MODES == ("Beginner", "Challenge", "Performance")
    for mode in GAME_MODES:
        assert normalize_game_mode(" " + mode + " ") == mode
    for value in [None, 1, True, {}, [], "", "beginner", "Advanced"]:
        with pytest.raises(ValueError):
            normalize_game_mode(value)


def test_fresh_schema_parity_and_constraints(db):
    command.upgrade(config(), "head")
    assert revision(db) == "w25weekly005"
    with db.connect() as c:
        assert compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata) == []
        cols = {x["name"]: x for x in inspect(c).get_columns("woodchuck_profiles")}
        assert cols["game_mode"]["nullable"] and cols["game_mode"]["default"] is None
        assert str(cols["game_mode"]["type"]) == "VARCHAR(20)"
        assert any(x["name"] == "ck_woodchuck_profile_game_mode" for x in inspect(c).get_check_constraints("woodchuck_profiles"))
        from app.operator_schema_compatibility import installed_revision
        with pytest.raises(RuntimeError, match="revision_not_approved"):
            installed_revision(c, RuntimeError)
    profile(db, 1)
    for value in ["Beginner", "Challenge", "Performance", None]:
        with db.begin() as c:
            c.execute(text("UPDATE woodchuck_profiles SET game_mode=:v WHERE id=1"), {"v": value})
    for invalid in ["beginner", "Advanced", "Performance ", ""]:
        before = rows(db)
        with pytest.raises(IntegrityError), db.begin() as c:
            c.execute(text("UPDATE woodchuck_profiles SET game_mode=:v WHERE id=1"), {"v": invalid})
        assert rows(db) == before


def test_bootstrap_never_infers_game_mode():
    for level in ["Beginner", "Intermediate", "Advanced", None, "Challenge"]:
        for state in [None, {}, *({"_weekly_preferences": {"game_mode": mode}} for mode in GAME_MODES)]:
            assert bootstrap_game_mode(level, state) is None


def test_populated_upgrade_requires_explicit_selection_preserves_all_history(db):
    command.upgrade(config(), OLD)
    seed_history(db)
    cases = [("Intermediate", {"_weekly_preferences": {"game_mode": "Challenge"}}, None),
             ("Advanced", {"_weekly_preferences": {"game_mode": "Performance"}}, None),
             ("Intermediate", {"_weekly_preferences": {"game_mode": "Beginner"}}, None),
             ("Advanced", {"_weekly_preferences": {"game_mode": "Performance "}}, None),
             ("Intermediate", json.dumps({"_weekly_preferences": {"game_mode": "Performance"}}), None),
             ("Intermediate", {"_weekly_preferences": {"game_mode": []}}, None),
             ("Beginner", {}, None), ("Intermediate", {}, None), ("Advanced", {}, None),
             ("Beginner", {"_weekly_preferences": {"game_mode": "Performance"}}, None)]
    for ident, (level, state, expected) in enumerate(cases, 2):
        profile(db, ident)
        with Session(db) as s:
            s.execute(text("UPDATE woodchuck_profiles SET level=:v WHERE id=:i"), {"v": level, "i": ident})
            s.add(m.WoodchuckState(profile_id=ident, state_json=state, revision=4))
            s.commit()
    before = rows(db)
    command.upgrade(config(), NEW)
    assert revision(db) == NEW and without_mode(rows(db)) == before
    with Session(db) as s:
        assert s.get(m.WoodchuckProfile, 1).game_mode is None
        for ident, (_, _, expected) in enumerate(cases, 2):
            assert s.get(m.WoodchuckProfile, ident).game_mode == expected
    after = rows(db)
    command.upgrade(config(), NEW)
    assert rows(db) == after


def test_unused_downgrade_preserves_rows_and_used_refuses(db):
    command.upgrade(config(), OLD)
    profile(db, 1)
    with db.begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET level='Intermediate' WHERE id=1"))
    before, structure = rows(db), schema_contract(db)
    command.upgrade(config(), NEW)
    command.downgrade(config(), OLD)
    assert revision(db) == OLD and rows(db) == before and schema_contract(db) == structure
    command.upgrade(config(), NEW)
    with db.begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET game_mode='Challenge' WHERE id=1"))
    used = rows(db)
    with pytest.raises(RuntimeError, match="game_mode_in_use"):
        command.downgrade(config(), OLD)
    assert revision(db) == NEW and rows(db) == used


@pytest.fixture
def runtime(db, monkeypatch):
    command.upgrade(config(), NEW)
    from app.main import app
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as s:
        for ident in (1, 2):
            s.add(m.WoodchuckProfile(id=ident, woodchuck_id=f"WL2-MODE-{ident}",
                display_name=f"Synthetic {ident}", pin_hash=hash_pin("2468"), instrument="Flute",
                level="Intermediate", level_changed_at=NOW-timedelta(days=100), goal="Practice"))
        s.flush()
        for ident in (1, 2):
            declare_age(s, ident, "adult")
            s.add(m.WoodchuckState(profile_id=ident, revision=0, state_json={
                "profile": {"level": "Intermediate"}, "progress": {"credits": 17, "level": 6},
                "_weekly_preferences": {"game_mode": "Performance"}}))
        s.commit()
    def client(ident=1, **kwargs):
        c = TestClient(app, **kwargs)
        response = c.post("/account/login", data={"woodchuck_id": f"WL2-MODE-{ident}", "pin": "2468"})
        assert response.status_code == 200, response.text
        return c
    return db, factory, client


def saved(factory, ident=1):
    with factory() as s:
        p, st = s.get(m.WoodchuckProfile, ident), s.get(m.WoodchuckState, ident)
        return p.game_mode, deepcopy(st.state_json), st.revision


def test_valid_invalid_idempotent_saves_and_level_compatibility(runtime):
    db, factory, client = runtime
    c = client()
    original = rows(db)
    for mode in GAME_MODES:
        response = c.patch(PATH, json={"game_mode": " " + mode + " "})
        assert response.status_code == 200 and response.json()["game_mode"] == mode
        value, data, rev = saved(factory)
        assert value == data["_weekly_preferences"]["game_mode"] == data["profile"]["game_mode"] == mode
        assert response.json()["revision"] == rev and response.json()["woodchuck_id"] == "WL2-MODE-1"
        assert "set-cookie" not in response.headers
        before = rows(db)
        assert c.patch(PATH, json={"game_mode": mode}).status_code == 200
        assert rows(db) == before
        # Re-running the current upgrade must never clear an explicit choice.
        command.upgrade(config(), NEW)
        assert rows(db) == before and saved(factory)[0] == mode
    for invalid in ["", "beginner", "Advanced", None, True, 7, {}, []]:
        before = rows(db)
        assert c.patch(PATH, json={"game_mode": invalid}).status_code in (400, 422)
        assert rows(db) == before
    # The old interface still changes only Level using its original cooldown.
    assert c.patch("/account/profile/level", json={"level": "Advanced"}).status_code == 200
    assert c.patch("/account/profile/level", json={"level": "Beginner"}).status_code == 429
    assert saved(factory)[0] == "Performance"
    after = rows(db)
    for table in original:
        if table not in ("woodchuck_profiles", "woodchuck_states"):
            assert after[table] == original[table], table
    with factory() as s:
        assert s.get(m.WoodchuckProfile, 1).level == "Advanced"
        assert s.get(m.WoodchuckProfile, 2).game_mode is None
    assert after["woodchuck_states"][0]["state_json"]["progress"] == original["woodchuck_states"][0]["state_json"]["progress"]


def test_canonical_projection_forged_and_stale_state(runtime):
    db, factory, client = runtime
    c = client()
    stale = c.get("/account/state").json()
    assert stale["state"]["_weekly_preferences"]["game_mode"] is None
    assert saved(factory)[1]["_weekly_preferences"]["game_mode"] == "Performance"  # Read does not rewrite old JSON.
    assert c.patch(PATH, json={"game_mode": "Challenge"}).status_code == 200
    stale["state"]["account"] = {"serverRevision": stale["revision"]}
    assert c.put("/account/state", json=stale["state"]).status_code == 409
    fresh = c.get("/account/state").json()
    data = fresh["state"]
    data["account"] = {"serverRevision": fresh["revision"], "woodchuckId": "WL2-MODE-2"}
    data["_weekly_preferences"]["game_mode"] = "Performance"
    data["profile"].update(game_mode="Performance", level="Legend")
    assert c.put("/account/state?profile_id=2", json=data).status_code == 200
    value, data, rev = saved(factory)
    assert value == data["profile"]["game_mode"] == data["_weekly_preferences"]["game_mode"] == "Challenge"
    assert data["profile"]["level"] == "Intermediate" and saved(factory, 2)[0] is None


def test_rollback_and_lost_response_retry(runtime, monkeypatch):
    db, factory, client = runtime
    c = client(raise_server_exceptions=False)
    before = rows(db)
    def fail_commit(s):
        if any(isinstance(p, m.WoodchuckProfile) and p.game_mode == "Challenge" for p in s.dirty):
            s.flush()  # Both rows written inside transaction, then rolled back.
            raise RuntimeError("synthetic interruption before commit")
    event.listen(factory.class_, "before_commit", fail_commit)
    try:
        assert c.patch(PATH, json={"game_mode": "Challenge"}).status_code == 500
    finally:
        event.remove(factory.class_, "before_commit", fail_commit)
    assert rows(db) == before
    from app import account_routes as routes
    original_response = routes.JSONResponse
    def lose_response(content, **kwargs):
        if content.get("updated") and content.get("game_mode"):
            raise RuntimeError("synthetic response lost after commit")
        return original_response(content, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(routes, "JSONResponse", lose_response)
        assert c.patch(PATH, json={"game_mode": "Challenge"}).status_code == 500
    value, data, rev = saved(factory)
    assert value == data["_weekly_preferences"]["game_mode"] == data["profile"]["game_mode"] == "Challenge"
    assert c.get("/account/state").json()["state"] == data
    before_retry = rows(db)
    retry = c.patch(PATH, json={"game_mode": "Challenge"})
    assert retry.status_code == 200 and retry.json()["revision"] == rev
    assert rows(db) == before_retry



def assert_only_session_retirement(before, after, cookie):
    # Stale/deleted credentials are retired by existing session middleware.
    # Require the exact nonce record; all account/economy/history tables stay equal.
    import base64
    from app.session_revocations import NONCE, digest
    nonce = json.loads(base64.b64decode(cookie.split(".")[0]))[NONCE]
    key = digest(nonce)
    for table in before:
        if table != "revoked_browser_sessions":
            assert after[table] == before[table], table
    old = {row["token_hash"]: row for row in before["revoked_browser_sessions"]}
    new = {row["token_hash"]: row for row in after["revoked_browser_sessions"]}
    assert set(new) == set(old) | {key}
    assert all(new[k] == row for k, row in old.items())
    assert new[key]["expires_at"] > new[key]["revoked_at"]


def test_origin_account_and_stale_credential_boundaries(runtime):
    db, factory, client = runtime
    c = client()
    for headers in [{"Origin": "http://foreign.invalid"}, {"Origin": "http://testserver:9999"},
                    {"Origin": ""}, {"Origin": "null"}, {"x-woodshed-account": "WL2-MODE-2"}]:
        before = rows(db)
        response = c.patch(PATH, json={"game_mode": "Performance"}, headers=headers)
        assert response.status_code == (401 if "x-woodshed-account" in headers else 403)
        assert rows(db) == before
    old_cookie = c.cookies.get("session")
    assert c.post("/account/login", data={"woodchuck_id": "WL2-MODE-2", "pin": "2468"}).status_code == 200
    before = rows(db)
    assert c.patch(PATH, json={"game_mode": "Challenge"}, headers={"x-woodshed-account": "WL2-MODE-1"}).status_code == 401
    from app.main import app
    stale = TestClient(app)
    stale.cookies.set("session", old_cookie)
    response = stale.patch(PATH, json={"game_mode": "Performance"})
    assert response.status_code == 401 and "set-cookie" not in response.headers
    assert rows(db) == before
    assert c.patch(PATH, json={"game_mode": "Beginner"}, headers={"x-woodshed-account": "WL2-MODE-2"}).status_code == 200
    assert saved(factory, 1)[0] is None and saved(factory, 2)[0] == "Beginner"
    with factory() as s:
        s.get(m.WoodchuckProfile, 2).session_version += 1
        s.commit()
    before = rows(db)
    cookie = c.cookies.get("session")
    assert c.patch(PATH, json={"game_mode": "Performance"}).status_code == 401
    assert_only_session_retirement(before, rows(db), cookie)


def test_ineligible_accounts_do_not_gain_mode_authority(runtime):
    db, factory, client = runtime
    c = client()
    for age in ["under13", "unknown"]:
        with factory() as s:
            s.get(AccountPrivacy, 1).age_band = age
            s.commit()
        before = rows(db)
        assert c.patch(PATH, json={"game_mode": "Challenge"}).status_code == 403
        assert rows(db) == before
    with factory() as s:
        s.get(m.WoodchuckProfile, 1).status = "deleted"
        s.commit()
    before = rows(db)
    cookie = c.cookies.get("session")
    assert c.patch(PATH, json={"game_mode": "Challenge"}).status_code == 401
    assert_only_session_retirement(before, rows(db), cookie)


def test_native_child_consent_and_withdrawal(db, monkeypatch):
    command.upgrade(config(), NEW)
    from app.main import app
    from app import tester_enrollments as testers, child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(testers, "clock", lambda: CLAIMED)
    # Established synthetic parent activation path; preserves its original assertions.
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(factory, monkeypatch, "/prebeta/C002", "C002", "DIRECTOR1")
    with factory() as s:
        p = s.scalar(select(m.WoodchuckProfile));wid, ident = p.woodchuck_id, p.id
    c = TestClient(app)
    assert c.post("/account/login", data={"woodchuck_id": wid, "pin": "2468"}).status_code == 200
    assert c.patch(PATH, json={"game_mode": "Performance"}).status_code == 200
    with factory() as s:
        consent.withdraw_evidence(s, s.scalar(select(ConsentEvidence)))
        s.commit()
    before = rows(db)
    cookie = c.cookies.get("session")
    assert c.patch(PATH, json={"game_mode": "Challenge"}).status_code == 401
    assert_only_session_retirement(before, rows(db), cookie)
    assert saved(factory, ident)[0] == "Performance"


@pytest.mark.parametrize("db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("second", ["mode", "state", "state-first", "revoked", "rotated"])
def test_postgres_wait_revalidates_and_serializes(runtime, second, record_property):
    db, factory, client = runtime
    c1, c2 = client(), client()
    # Reuse one authenticated identity for the retired-nonce scenario.
    if second == "revoked":
        c2.cookies.update(c1.cookies)
    stale = c2.get("/account/state").json()
    stale["state"]["account"] = {"serverRevision": stale["revision"]}
    initial_revision = saved(factory)[2]
    held, release = Event(), Event()
    pids = {}
    def hold_commit(s):
        if not held.is_set() and (second == "state-first" or any(isinstance(p, m.WoodchuckProfile) and p.game_mode == "Challenge" for p in s.dirty)):
            pids["first"] = s.scalar(text("SELECT pg_backend_pid()"))
            assert s.connection().get_isolation_level() == "READ COMMITTED"
            held.set()
            assert release.wait(15)
            if second == "rotated":
                s.get(m.WoodchuckProfile, 1).session_version += 1
    def query(c, cursor, statement, parameters, context, many):
        if "woodchuck_profiles" in statement and "FOR UPDATE" in statement and held.is_set():
            # SQLAlchemy's psycopg connection provides its PID without issuing SQL.
            pids["second"] = c.connection.driver_connection.info.backend_pid
    event.listen(factory.class_, "before_commit", hold_commit)
    event.listen(db, "before_cursor_execute", query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(c1.put, "/account/state", json=stale["state"]) if second == "state-first" else pool.submit(c1.patch, PATH, json={"game_mode": "Challenge"})
            assert held.wait(10)
            future = pool.submit(c2.put, "/account/state", json=stale["state"]) if second == "state" else pool.submit(c2.patch, PATH, json={"game_mode": "Performance"})
            blockers = []
            try:
                deadline = time.monotonic() + 8
                with db.connect() as observer:
                    while time.monotonic() < deadline:
                        if "second" in pids:
                            blockers = observer.scalar(text("SELECT pg_blocking_pids(:p)"), {"p": pids["second"]})
                            if pids["first"] in blockers:
                                break
                        time.sleep(.01)
                assert pids["first"] in blockers, "No genuine PostgreSQL blocking observed"
                record_property("postgres_blocking", json.dumps({"case": second, "pids": pids, "blockers": blockers, "isolation": "READ COMMITTED"}))
                if second == "revoked":
                    assert c1.post("/account/logout").status_code == 200
            finally:
                release.set()
            assert first.result(timeout=15).status_code == 200
            response = future.result(timeout=15)
            assert response.status_code == {"mode": 200, "state": 409, "state-first": 200, "revoked": 401, "rotated": 401}[second]
    finally:
        release.set()
        event.remove(factory.class_, "before_commit", hold_commit)
        event.remove(db, "before_cursor_execute", query)
    value, data, rev = saved(factory)
    assert value == data["_weekly_preferences"]["game_mode"] == data["profile"]["game_mode"] == ("Performance" if second in ("mode", "state-first") else "Challenge")
    assert rev == initial_revision + (2 if second in ("mode", "state-first") else 1)
    assert saved(factory, 2)[0] is None


def test_delayed_a_response_does_not_replace_b_session(runtime, monkeypatch):
    db, factory, client = runtime
    c = client()
    from app import account_routes as routes
    original = routes.JSONResponse
    held, release = Event(), Event()
    def delayed(content, **kwargs):
        if content.get("updated") and content.get("game_mode"):
            held.set()
            assert release.wait(12)
        return original(content, **kwargs)
    monkeypatch.setattr(routes, "JSONResponse", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(c.patch, PATH, json={"game_mode": "Challenge"}, headers={"x-woodshed-account": "WL2-MODE-1"})
        try:
            assert held.wait(8)
            assert c.post("/account/login", data={"woodchuck_id": "WL2-MODE-2", "pin": "2468"}).status_code == 200
            b_before = saved(factory, 2)
        finally:
            release.set()
        late = pending.result(timeout=12)
    assert late.status_code == 200 and late.json()["woodchuck_id"] == "WL2-MODE-1"
    assert "set-cookie" not in late.headers
    assert c.get("/account/me").json()["profile"]["woodchuck_id"] == "WL2-MODE-2"
    assert saved(factory, 2) == b_before and b_before[0] is None
    assert saved(factory, 1)[0] == "Challenge"


def test_creation_ignores_forged_mode_and_missing_state_is_atomic(db, monkeypatch):
    command.upgrade(config(), NEW)
    from app.main import app
    from tests.test_tester_enrollments import account_form
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    c = TestClient(app)
    form = account_form()
    form["initial_state"] = json.dumps({"_weekly_preferences": {"game_mode": "Performance"},
        "profile": {"game_mode": "Challenge"}, "progress": {"credits": 99999}})
    result = c.post("/account/create", data=form)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["profile"]["game_mode"] is None and payload["profile"]["level"] == "Beginner"
    assert payload["state"]["profile"]["game_mode"] is None
    assert payload["state"]["_weekly_preferences"]["game_mode"] is None
    ident = payload["profile"]["id"]
    with factory() as s:
        assert s.get(m.WoodchuckProfile, ident).game_mode is None
        assert s.get(m.WoodchuckState, ident).state_json["progress"]["credits"] != 99999
        # A synthetic legacy account without a state row still needs one atomic save.
        s.delete(s.get(m.WoodchuckState, ident))
        s.commit()
    before = rows(db)
    response = c.patch(PATH, json={"game_mode": "Challenge"})
    assert response.status_code == 200
    value, data, rev = saved(factory, ident)
    assert value == data["_weekly_preferences"]["game_mode"] == "Challenge" and rev == 1
    after = rows(db)
    assert after["reward_grants"] == before["reward_grants"]
    assert after["contest_results"] == before["contest_results"]
    before_retry = rows(db)
    assert c.patch(PATH, json={"game_mode": "Challenge"}).status_code == 200
    assert rows(db) == before_retry
