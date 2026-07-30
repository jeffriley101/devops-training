from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import account_routes
from app.db import Base
from app.models import WoodchuckProfile, WoodchuckState


ROOT = Path(__file__).resolve().parents[1]


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
    assert 'method: "PUT"' not in javascript
    assert account_js.count('method: "PUT"') == 2  # explicit account creation + sync


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
