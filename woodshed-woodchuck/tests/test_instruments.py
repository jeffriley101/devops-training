from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import account_routes
from app.contests import normalize_instrument
from app.account_routes import InstrumentUpdate, change_profile_instrument
from app.accounts import create_woodchuck_profile, update_profile_instrument
from app.db import Base
from app.instruments import (
    INSTRUMENT_DEFINITIONS,
    INSTRUMENT_OPTIONS,
    canonical_instrument_key,
    normalize_supported_instrument,
    shed_artwork_url,
)
from app.models import (
    PracticeChart,
    PracticeChartVerification,
    StudentVerifierConnection,
    TrustedVerifier,
    WoodchuckProfile,
    WoodchuckState,
)
from app.practice_charts import create_practice_chart_verification_request


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.mark.parametrize("instrument", INSTRUMENT_OPTIONS)
def test_every_supported_instrument_is_accepted(instrument: str) -> None:
    assert normalize_supported_instrument(instrument) == instrument
    assert normalize_supported_instrument(f"  {instrument.lower()}  ") == instrument


@pytest.mark.parametrize("instrument", ["", "Oboe", "Kazoo", "Sax", None])
def test_unsupported_instruments_are_rejected(instrument: object) -> None:
    with pytest.raises(ValueError, match="supported instrument"):
        normalize_supported_instrument(instrument)  # type: ignore[arg-type]


def test_account_creation_rejects_an_unsupported_instrument(
    session: Session,
) -> None:
    with pytest.raises(ValueError, match="supported instrument"):
        create_woodchuck_profile(
            session,
            display_name="Public Chuck",
            pin="1234",
            instrument="Oboe",
            level="Beginner",
            goal="Practice",
        )


def test_instrument_change_preserves_account_state_and_history(
    session: Session,
) -> None:
    profile = WoodchuckProfile(
        woodchuck_id="WC-KEEP-ME",
        display_name="Public Chuck",
        pin_hash="unchanged-pin-hash",
        instrument="Saxophone",
        level="Advanced",
        goal="Build daily consistency",
    )
    session.add(profile)
    session.flush()
    saved_state = {
        "profile": {"instrument": "Saxophone", "level": "Advanced"},
        "progress": {"credits": 321, "streak": 14, "level": 8},
        "bandCamp": {"totals": {"points": 27}},
        "inventory": {"medals": ["gold"], "crowns": ["commitment"]},
        "practiceLog": [{"instrument": "Saxophone", "minutes": 30}],
        "history": {"seasons": ["Band Camp 2025"]},
    }
    state = WoodchuckState(profile_id=profile.id, state_json=deepcopy(saved_state))
    old_chart = PracticeChart(
        profile_id=profile.id,
        practice_date=date(2026, 7, 27),
        minutes=30,
        instrument="Saxophone",
        practice_details=[],
        source="p-book",
        credits_awarded=5,
    )
    verifier = TrustedVerifier(
        email="private@example.com",
        display_name="Private Verifier",
        pin_hash="verifier-hash",
    )
    session.add_all([state, old_chart, verifier])
    session.flush()
    connection = StudentVerifierConnection(
        profile_id=profile.id,
        verifier_id=verifier.id,
        role="teacher",
        status="accepted",
    )
    verification = PracticeChartVerification(
        practice_chart_id=old_chart.id,
        verifier_id=verifier.id,
        status="approved",
    )
    session.add_all([connection, verification])
    session.commit()

    original = {
        "id": profile.id,
        "woodchuck_id": profile.woodchuck_id,
        "pin_hash": profile.pin_hash,
        "display_name": profile.display_name,
        "level": profile.level,
        "goal": profile.goal,
    }
    update_profile_instrument(session, profile=profile, instrument="Clarinet")

    for field, value in original.items():
        assert getattr(profile, field) == value
    assert profile.instrument == "Clarinet"
    assert state.state_json == saved_state
    assert old_chart.instrument == "Saxophone"
    assert connection.status == "accepted"
    assert verification.status == "approved"

    future = create_practice_chart_verification_request(
        session,
        profile=profile,
        verifier_id=verifier.id,
        practice_date=date(2026, 8, 4),
        minutes=20,
    )
    assert future.chart.instrument == "Clarinet"
    assert old_chart.instrument == "Saxophone"


def test_instrument_definitions_and_shed_assets_are_complete() -> None:
    definitions = {item["label"]: item for item in INSTRUMENT_DEFINITIONS}
    assert list(definitions) == INSTRUMENT_OPTIONS
    assert len(definitions) == 17
    assert definitions["Clarinet"]["image_url"].endswith("clarinet.svg")
    assert definitions["Tuba"]["image_url"].endswith("tuba.svg")
    assert definitions["Drum Major"]["image_url"] is None
    assert definitions["Drum Major"]["fallback_symbol"] == "🫡"
    assert definitions["Color Guard"]["image_url"] is None
    assert definitions["Color Guard"]["fallback_symbol"] == "🚩"
    assert definitions["Vocals"]["fallback_symbol"] == "🎤"
    assert "Hand Percussion" not in definitions

    static_dir = Path(__file__).resolve().parents[1] / "static"
    for label in ("Clarinet", "Tuba"):
        image_path = definitions[label]["image_url"].removeprefix("/static/")
        assert (static_dir / image_path).is_file()
    assert not (static_dir / "img/instruments/drum-major.svg").exists()
    assert not (static_dir / "img/instruments/color-guard.svg").exists()


def test_hand_percussion_is_legacy_only_and_vocals_is_selectable() -> None:
    assert "Vocals" in INSTRUMENT_OPTIONS
    assert normalize_supported_instrument(" vocals ") == "Vocals"
    assert "Hand Percussion" not in INSTRUMENT_OPTIONS
    with pytest.raises(ValueError, match="supported instrument"):
        normalize_supported_instrument("Hand Percussion")

    assert canonical_instrument_key("Hand Percussion") == "percussion"
    assert canonical_instrument_key("hand-percussion") == "percussion"
    assert shed_artwork_url("Hand Percussion") == "/static/img/shed-cabin-new.png"


def test_historical_hand_percussion_p_chart_remains_readable(
    session: Session,
) -> None:
    profile = WoodchuckProfile(
        woodchuck_id="WC-LEGACY-HAND-PERCUSSION",
        display_name="Legacy Player",
        pin_hash="legacy-pin-hash",
        instrument="Hand Percussion",
        level="Intermediate",
        goal="Keep practicing",
    )
    session.add(profile)
    session.commit()

    created = create_practice_chart_verification_request(
        session,
        profile=profile,
        verifier_id=None,
        practice_date=date(2026, 8, 31),
        minutes=20,
    )

    assert created.chart.instrument == "Hand Percussion"
    assert normalize_instrument(created.chart.instrument) == (
        "percussion", "Percussion",
    )


def test_shed_uses_dynamic_instrument_renderer_and_safe_profile_form() -> None:
    root = Path(__file__).resolve().parents[1]
    home = (root / "templates" / "home.html").read_text(encoding="utf-8")
    renderer = (root / "static/js/instruments.js").read_text(encoding="utf-8")
    account_js = (root / "static/js/account.js").read_text(encoding="utf-8")

    assert ">\n          🎷\n" not in home
    assert 'id="change-instrument-form"' in home
    assert "Changing instruments will not erase your practice history" in home
    assert "renderInstrument" in renderer
    assert 'fetch("/account/profile/instrument"' in account_js
    assert 'credentials: "same-origin"' in account_js
    for private_field in ("pin_hash", "verifier_email", "verifier_name"):
        assert private_field not in home.casefold()
        assert private_field not in renderer.casefold()


def test_instrument_endpoint_requires_authentication() -> None:
    request = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/account/profile/instrument",
            "headers": [],
            "query_string": b"",
            "session": {},
        }
    )
    with pytest.raises(Exception) as raised:
        change_profile_instrument(request, InstrumentUpdate(instrument="Tuba"))
    assert getattr(raised.value, "status_code", None) == 401


def test_authenticated_instrument_api_returns_only_public_instrument_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-PRIVATE",
            display_name="Public Chuck",
            pin_hash="private-pin-hash",
            instrument="Flute",
            level="Beginner",
            goal="Practice",
        )
        session.add(profile)
        session.commit()
        profile_id = profile.id

    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    request = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/account/profile/instrument",
            "headers": [],
            "query_string": b"",
            "session": {account_routes.SESSION_PROFILE_ID: profile_id},
        }
    )
    payload = change_profile_instrument(
        request,
        InstrumentUpdate(instrument="Tuba"),
    )

    assert set(payload) == {
        "updated", "instrument", "instrument_definition", "shed_artwork_url"
    }
    assert payload["instrument"] == "Tuba"
    assert payload["shed_artwork_url"] == "/static/img/shed-cabin-new.png"
    with factory() as session:
        persisted = session.get(WoodchuckProfile, profile_id)
        assert persisted is not None
        assert persisted.instrument == "Tuba"
    serialized = repr(payload).casefold()
    for private_value in (
        "wc-private",
        "private-pin-hash",
        "profile_id",
        "woodchuck_id",
        "verifier",
        "email",
    ):
        assert private_value not in serialized
