from __future__ import annotations

import json
from pathlib import Path
import subprocess

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import account_routes
from app.db import Base
from app.models import WoodchuckProfile, WoodchuckState


ROOT = Path(__file__).resolve().parents[1]


def test_full_setup_browser_sequence_has_atomic_handoff_and_one_later_put(tmp_path) -> None:
    """Run the setup page's real scripts in their production initialization order."""
    sources = [
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "static/js/state.js", "static/js/instruments.js",
            "static/js/app.js", "static/js/account.js",
        )
    ]
    harness = r'''
class Events {
  constructor() { this.listeners = {}; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatchEvent(event) {
    event.target ||= this;
    for (const callback of this.listeners[event.type] || []) callback.call(this, event);
    return !event.defaultPrevented;
  }
}
class Element extends Events {
  constructor(id = "") {
    super(); this.id = id; this.value = ""; this.textContent = "";
    this.hidden = false; this.disabled = false; this.dataset = {};
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  querySelector(selector) { return this.queries?.[selector] || null; }
  querySelectorAll() { return []; }
  setAttribute() {}
  append() {}
  appendChild() {}
  replaceChildren() {}
  scrollIntoView() {}
  focus() {}
  closest() { return null; }
}
const ids = new Map();
const element = (id) => { const value = new Element(id); ids.set(id, value); return value; };
const form = element("account-create-form");
const name = element("woodchuck-name"); name.value = "Browser Chuck";
const instrument = element("instrument"); instrument.value = "Flute";
const level = element("level"); level.value = "Beginner";
const goal = element("goal"); goal.value = "Practice every day";
const pin = element("student-pin"); pin.value = "2468";
const submit = new Element(); submit.textContent = "Create Persistent Woodchuck";
form.queries = {
  "#woodchuck-name": name, "#instrument": instrument, "#level": level,
  "#goal": goal, "button[type='submit']": submit,
};
form._entries = {display_name: name.value, instrument: instrument.value,
  level: level.value, goal: goal.value, pin: pin.value};
for (const id of ["setup-error", "account-created-panel", "created-woodchuck-id",
  "created-pin", "copy-woodchuck-id", "copy-account-feedback"]) element(id);
const bootstrap = element("account-state-bootstrap"); bootstrap.textContent = "null";
for (const id of ["quest-pool-data", "sax-viking-messages-data", "instrument-definitions-data"])
  element(id).textContent = "{}";

global.CustomEvent = class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } };
global.FormData = class {
  constructor(target) { this.values = {...target._entries}; }
  get(key) { return this.values[key]; }
  set(key, value) { this.values[key] = value; }
};
global.document = {
  getElementById: (id) => ids.get(id) || null,
  querySelector: () => null, querySelectorAll: () => [],
  createElement: () => new Element(), body: new Element("body"),
};
const storage = new Map();
const windowEvents = new Events();
global.window = Object.assign(windowEvents, {
  localStorage: {getItem: (key) => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key)},
  clearTimeout, setTimeout, matchMedia: () => ({matches: true}),
  location: {assign() {}, reload() {}}, alert() {},
});
global.navigator = {clipboard: {writeText: async () => {}}};
const requests = [];
global.fetch = async (url, options = {}) => {
  const method = options.method || "GET";
  requests.push({url, method, body: options.body});
  if (url === "/account/create") return {
    ok: true, status: 200, json: async () => ({
      profile: {woodchuck_id: "WC-BROWSER", display_name: "Browser Chuck",
        instrument: "Flute", level: "Beginner", goal: "Practice every day"},
      credentials: {woodchuck_id: "WC-BROWSER", pin: "2468"}, revision: 0,
      state: {version: 4, account: {woodchuckId: "WC-BROWSER", authenticated: true,
        serverRevision: 0}, profile: {woodchuckName: "Browser Chuck", instrument: "Flute",
        level: "Beginner", goal: "Practice every day"}, progress: {credits: 0}}
    })};
  if (url === "/account/state" && method === "PUT") {
    const sent = JSON.parse(options.body);
    return {ok: true, status: 200, json: async () => ({revision: sent.account.serverRevision + 1})};
  }
  return {ok: true, status: 200, json: async () => ({})};
};
'''
    script = tmp_path / "setup-flow.js"
    script.write_text(
        harness + "\n" + "\n".join(sources) + r'''
(async () => {
  form.dispatchEvent({type: "submit", preventDefault() { this.defaultPrevented = true; }});
  await new Promise((resolve) => setTimeout(resolve, 25));
  const creationRequests = requests.map(({url, method}) => ({url, method}));
  const installed = window.WWState.getState();
  window.WWState.saveState(installed);
  await new Promise((resolve) => setTimeout(resolve, 650));
  const puts = requests.filter((request) => request.url === "/account/state" && request.method === "PUT");
  console.log(JSON.stringify({creationRequests, requests: requests.map(({url, method}) => ({url, method})),
    id: ids.get("created-woodchuck-id").textContent, pin: ids.get("created-pin").textContent,
    panelHidden: ids.get("account-created-panel").hidden, revisionBeforeEdit: installed.account.serverRevision,
    putRevision: puts.length ? JSON.parse(puts[0].body).account.serverRevision : null,
    finalRevision: window.WWState.getState().account.serverRevision}));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''', encoding="utf-8")
    completed = subprocess.run(
        ["node", str(script)], check=True, capture_output=True, text=True,
    )
    result = json.loads(completed.stdout.strip())
    assert result["creationRequests"] == [{"url": "/account/create", "method": "POST"}]
    assert result["id"] == "WC-BROWSER" and result["pin"] == "2468"
    assert result["panelHidden"] is False
    assert result["revisionBeforeEdit"] == result["putRevision"] == 0
    assert result["requests"] == [
        {"url": "/account/create", "method": "POST"},
        {"url": "/account/state", "method": "PUT"},
    ]
    assert result["finalRevision"] == 1


def request_with_profile(profile_id: int) -> Request:
    return Request({
        "type": "http",
        "method": "PUT",
        "path": "/account/state",
        "headers": [],
        "query_string": b"",
        "session": {account_routes.SESSION_PROFILE_ID: profile_id},
    })


def test_page_initialization_and_server_hydration_do_not_request_state_put() -> None:
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")
    state_js = (ROOT / "static/js/state.js").read_text(encoding="utf-8")

    initialization = javascript[javascript.index("const state = ensureTodayQuest"):]
    assert "stateApi.saveState(state);" not in initialization
    assert "stateApi.saveState(next, { sync: false });\n      hydrateHome(next);" in javascript
    assert "stateApi.saveState(next, { sync: false });\n      renderBoard(next);" in javascript
    assert "stateApi.saveState(next, { sync: false });\n        renderEntries(next);" in javascript
    assert "saveState(restored, { sync: false })" in state_js
    assert 'fetch("/account/state"' not in javascript
    assert javascript.count('method: "PUT"') == 2
    placement = javascript[
        javascript.index("async function savePlacement"):
        javascript.index("async function removePlacement")
    ]
    assert 'method: "PUT"' in placement
    assert "/store/inventory/${item.id}/placement" in placement
    size_preference = javascript[
        javascript.index("async function savePreferredSize"):
        javascript.index("function overlapsPlaced")
    ]
    assert 'method: "PUT"' in size_preference
    assert "/store/inventory/${item.id}/size" in size_preference
    assert account_js.count('method: "PUT"') == 2  # missing-state login + sync
    create_flow = account_js[
        account_js.index("function wireCreateAccount"):
        account_js.index("function wireLogin")
    ]
    assert 'method: "PUT"' not in create_flow
    assert "await uploadState" not in create_flow
    assert 'formData.set(\n          "initial_state"' in create_flow
    assert "stateApi.saveState(state, { sync: false })" in create_flow
    assert "serverRevision" in create_flow


def test_create_atomically_returns_authoritative_state_and_credentials(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(account_routes, "SessionLocal", sessions)
    request = Request({
        "type": "http", "method": "POST", "path": "/account/create",
        "headers": [], "query_string": b"", "session": {},
    })
    browser_state = {
        "account": {
            "woodchuckId": "WC-OTHER",
            "authenticated": True,
            "serverRevision": 12,
        },
        "profile": {"woodchuckName": "Someone Else"},
        "progress": {"credits": 9},
    }

    result = account_routes.create_account(
        request,
        display_name="New Chuck",
        pin="2468",
        instrument="Flute",
        level="Beginner",
        goal="Practice every day",
        initial_state=json.dumps(browser_state),
    )

    assert result["authenticated"] is True
    assert result["revision"] == 1
    assert result["credentials"] == {
        "woodchuck_id": result["profile"]["woodchuck_id"],
        "pin": "2468",
    }
    assert result["state"]["account"] == {
        "woodchuckId": result["profile"]["woodchuck_id"],
        "authenticated": True,
        "serverRevision": 0,
        "lastSyncedAt": None,
    }
    assert result["state"]["profile"]["woodchuckName"] == "New Chuck"
    assert result["state"]["progress"]["credits"] == 10
    assert result["login_streak"]["current_streak"] == 1
    assert result["login_streak"]["dandelions_awarded"] == 1
    with sessions() as session:
        assert len(session.query(WoodchuckProfile).all()) == 1
        assert len(session.query(WoodchuckState).all()) == 1
        saved = session.query(WoodchuckState).one()
        assert saved.revision == 1
        assert saved.state_json == result["state"]


def test_create_success_ui_shows_id_and_pin_without_redundant_save() -> None:
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")
    setup = (ROOT / "templates/setup.html").read_text(encoding="utf-8")
    create_flow = account_js[
        account_js.index("function wireCreateAccount"):
        account_js.index("function wireLogin")
    ]
    assert 'id="created-woodchuck-id"' in setup
    assert 'id="created-pin"' in setup
    assert "payload.credentials.pin" in create_flow
    assert "successPanel.hidden = false" in create_flow
    assert 'fetch("/account/create"' in create_flow
    assert 'fetch("/account/state"' not in create_flow


def test_repeated_create_does_not_create_a_second_account(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(account_routes, "SessionLocal", sessions)
    request = Request({
        "type": "http", "method": "POST", "path": "/account/create",
        "headers": [], "query_string": b"", "session": {},
    })
    fields = {
        "display_name": "New Chuck", "pin": "2468", "instrument": "Flute",
        "level": "Beginner", "goal": "Practice every day",
        "initial_state": json.dumps({"progress": {"credits": 0}}),
    }
    account_routes.create_account(request, **fields)

    try:
        account_routes.create_account(request, **fields)
    except Exception as error:
        assert getattr(error, "status_code", None) == 400
        assert "already signed in" in str(getattr(error, "detail", ""))
    else:
        raise AssertionError("A repeated create should be rejected")

    with sessions() as session:
        assert len(session.query(WoodchuckProfile).all()) == 1
        assert len(session.query(WoodchuckState).all()) == 1


def test_programmatic_controls_do_not_save_but_real_edit_saves_once() -> None:
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")
    prefill = account_js[
        account_js.index("function prefillCreateForm"):
        account_js.index("function wireCreateAccount")
    ]
    profile_change = account_js[
        account_js.index("function wireProfileChange"):
        account_js.index("wireCreateAccount();")
    ]

    assert ".value =" in prefill
    assert "saveState(" not in prefill
    assert profile_change.count("stateApi.saveState(next);") == 1
    assert 'form.addEventListener("submit"' in profile_change


def test_sync_uses_revision_and_conflict_recovery_does_not_retry_loop() -> None:
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")
    sync = account_js[
        account_js.index("async function syncStateToServer"):
        account_js.index("function scheduleSync")
    ]
    recovery = account_js[
        account_js.index("async function recoverFromConflict"):
        account_js.index("async function syncStateToServer")
    ]

    assert "body: JSON.stringify(state)" in sync
    assert "serverRevision" in account_js
    assert "response.status === 409" in sync
    assert "pendingSync = false" in sync
    assert 'method: "PUT"' not in recovery
    assert 'fetch("/account/state"' in recovery
    assert "stateApi.saveState(payload.state, { sync: false })" in recovery
    assert "window.alert(" in recovery and "window.location.reload()" in recovery


def test_stale_state_is_rejected_without_overwriting_newer_account_data(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-SYNC", display_name="Student", pin_hash="private",
            instrument="Flute", level="Beginner", goal="Practice",
        )
        session.add(profile)
        session.flush()
        session.add(WoodchuckState(
            profile_id=profile.id,
            state_json={
                "account": {"serverRevision": 4},
                "profile": {"woodchuckName": "Student"},
                "progress": {"credits": 17},
            },
            revision=4,
        ))
        session.commit()
        profile_id = profile.id

    monkeypatch.setattr(account_routes, "SessionLocal", sessions)
    stale = {
        "account": {"serverRevision": 3},
        "profile": {"woodchuckName": "Stale"},
        "progress": {"credits": 0},
    }
    response = account_routes.save_account_state(
        request_with_profile(profile_id), stale
    )

    assert response.status_code == 409
    assert json.loads(response.body)["server_revision"] == 4
    with sessions() as session:
        saved = session.get(WoodchuckState, profile_id)
        profile = session.get(WoodchuckProfile, profile_id)
        assert saved.revision == 4
        assert saved.state_json["progress"]["credits"] == 17
        assert profile.display_name == "Student"
        assert profile.woodchuck_id == "WC-SYNC"
        assert profile.pin_hash == "private"

    current = {
        "account": {"serverRevision": 4},
        "profile": {"woodchuckName": "Student"},
        "progress": {"credits": 18},
    }
    result = account_routes.save_account_state(
        request_with_profile(profile_id), current
    )
    assert result["saved"] is True
    assert result["revision"] == 5
    with sessions() as session:
        saved = session.get(WoodchuckState, profile_id)
        assert saved.revision == 5
        assert saved.state_json["progress"]["credits"] == 18
