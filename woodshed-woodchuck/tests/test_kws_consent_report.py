"""Disposable KWS consent inventory fixtures; no production connection."""
from datetime import datetime, timedelta, timezone
import hashlib
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import kws_consent_report as inventory
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence, PendingConsent
from app.db import Base
from app.kws_models import KWSVerification
from app.models import WoodchuckProfile


NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("KWS_") or name in {"APP_ENV", "DATABASE_URL"}:
            monkeypatch.delenv(name)
    path = tmp_path / "report.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    yield engine, path
    engine.dispose()


def student(session, tag, *, complete_consent=False):
    profile = WoodchuckProfile(woodchuck_id=f"WC-REPORT-{tag}", display_name=f"Child {tag}",
                               pin_hash="SECRET_PIN_HASH", instrument="Flute", level="A", goal="B")
    session.add(profile)
    session.flush()
    rule = AccountPrivacy(profile_id=profile.id, age_band="under13", declared_at=NOW)
    session.add(rule)
    if complete_consent:
        evidence = ConsentEvidence(profile_id=profile.id, parent_email="secret-parent@example.test",
                                   notice_version="test-notice", notice_sha256="test-digest",
                                   approved_at=NOW, confirmed_at=NOW)
        session.add(evidence)
        session.flush()
        rule.consent_id = evidence.id
    return profile


def attempt(session, profile, tag, *, state=None, created=NOW, expired=False):
    pending = PendingConsent(profile_id=profile.id, parent_email="secret-parent@example.test",
                             director_email="secret-director@example.test", director_name="Secret Director",
                             review_allowed=False, approve_hash=f"SECRET_APPROVE_HASH_{tag}",
                             notice_version="test-notice", created_at=created,
                             expires_at=NOW - timedelta(minutes=1) if expired else NOW + timedelta(hours=48))
    session.add(pending)
    session.flush()
    if state:
        verification = KWSVerification(pending_id=pending.id, payload_hash=f"SECRET_PAYLOAD_HASH_{tag}",
                                       environment="test", org_id="SECRET_ORG", binding_sha256="SECRET_BINDING",
                                       notice_sha256="test-digest", guardian_attested_at=created,
                                       notice_accepted_at=created, account_allowed=True, director_allowed=False,
                                       created_at=created, expires_at=pending.expires_at, state=state,
                                       completed_at=created if state in {"verified", "failed"} else None,
                                       transaction_hash=f"SECRET_TRANSACTION_{tag}" if state == "verified" else None)
        session.add(verification)
    return pending


def test_categories_summary_and_repeated_attempts(database):
    engine, path = database
    with Session(engine) as session, session.begin():
        student(session, "complete", complete_consent=True)
        student(session, "no-flow")
        attempt(session, student(session, "no-kws"), "no-kws")
        attempt(session, student(session, "unknown"), "unknown", state="delivery_unknown")
        attempt(session, student(session, "reserved"), "reserved", state="reserved")
        attempt(session, student(session, "accepted"), "accepted", state="accepted")
        attempt(session, student(session, "verified"), "verified", state="verified")
        attempt(session, student(session, "failed"), "failed", state="failed")
        attempt(session, student(session, "cancelled"), "cancelled", state="cancelled")
        attempt(session, student(session, "expired"), "expired", expired=True)
        repeat = student(session, "repeat")
        attempt(session, repeat, "repeat-old", state="failed", created=NOW - timedelta(days=1))
        attempt(session, repeat, "repeat-new", state="delivery_unknown", created=NOW)
    result = inventory.report(f"sqlite:///{path}", now=NOW)
    assert result["total_under13_accounts_inspected"] == 11
    assert result["complete_consent_evidence"] == 1
    assert result["without_complete_consent_evidence"] == 10
    assert result["runtime_policy_eligibility_evaluated"] is False
    assert result["repeated_attempts"] == 1
    assert result["classification_counts"] == {
        "kws_cancelled": 1, "pending_expired": 1,
        "kws_failed": 1, "kws_delivery_unknown": 2,
        "kws_incomplete": 2, "pending_no_kws": 1,
        "under13_no_flow": 1, "verified_not_activated": 1,
    }
    by_id = {row["woodchuck_id"]: row for row in result["accounts"]}
    assert "WC-REPORT-complete" not in by_id
    assert by_id["WC-REPORT-repeat"]["pending_attempt_count"] == 2
    assert by_id["WC-REPORT-repeat"]["kws_state"] == "delivery_unknown"


def test_sensitive_fields_absent_and_database_unchanged(database, capsys):
    engine, path = database
    with Session(engine) as session, session.begin():
        profile = student(session, "private")
        attempt(session, profile, "private", state="delivery_unknown")
    before = hashlib.sha256(path.read_bytes()).digest()
    url = f"sqlite:///{path}"
    assert inventory.main(["--database-url", url]) == 0
    output = capsys.readouterr().out
    assert "kws_delivery_unknown" in output
    assert "Runtime-policy eligibility: not evaluated" in output
    for secret in ("secret-parent", "secret-director", "SECRET_PIN_HASH", "SECRET_APPROVE_HASH",
                   "SECRET_PAYLOAD_HASH", "SECRET_TRANSACTION", "SECRET_BINDING", "SECRET_ORG"):
        assert secret not in output
    assert hashlib.sha256(path.read_bytes()).digest() == before
    with inventory.readonly_connection(url) as connection:
        with pytest.raises(Exception):
            connection.execute(text("UPDATE woodchuck_profiles SET display_name = 'changed'"))


def test_explicit_target_required(database, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert inventory.main([]) == 1
    assert "Supply --database-url" in capsys.readouterr().err


def test_inactive_linked_consent_and_activated_anomaly(database):
    engine, path = database
    with Session(engine) as session, session.begin():
        withdrawn = student(session, "withdrawn", complete_consent=True)
        evidence = session.get(ConsentEvidence, session.get(AccountPrivacy, withdrawn.id).consent_id)
        evidence.withdrawn_at = NOW
        attempt(session, student(session, "activated-anomaly"), "activated", state="activated")
    result = inventory.report(f"sqlite:///{path}", now=NOW)
    by_id = {row["woodchuck_id"]: row for row in result["accounts"]}
    assert by_id["WC-REPORT-withdrawn"]["consent_status"] == "withdrawn"
    assert by_id["WC-REPORT-withdrawn"]["classification"] == "withdrawn_consent_no_flow"
    assert by_id["WC-REPORT-activated-anomaly"]["classification"] == "activated_without_complete_evidence"


def test_missing_invalid_and_incomplete_consent_evidence(database):
    engine, path = database
    with Session(engine) as session, session.begin():
        donor = student(session, "donor", complete_consent=True)
        donor_consent_id = session.get(AccountPrivacy, donor.id).consent_id
        missing = student(session, "missing")
        session.get(AccountPrivacy, missing.id).consent_id = 999999
        invalid = student(session, "invalid")
        session.get(AccountPrivacy, invalid.id).consent_id = donor_consent_id
        incomplete = student(session, "incomplete", complete_consent=True)
        session.get(ConsentEvidence, session.get(AccountPrivacy, incomplete.id).consent_id).parent_email = None
    result = inventory.report(f"sqlite:///{path}", now=NOW)
    by_id = {row["woodchuck_id"]: row for row in result["accounts"]}
    assert "WC-REPORT-donor" not in by_id
    for tag in ("missing", "invalid", "incomplete"):
        assert by_id[f"WC-REPORT-{tag}"]["consent_status"] == tag
        assert by_id[f"WC-REPORT-{tag}"]["classification"] == f"{tag}_consent_no_flow"


def test_explicit_url_without_kws_runtime_or_database_env(database, capsys):
    engine, path = database
    with Session(engine) as session, session.begin():
        student(session, "complete", complete_consent=True)
        student(session, "stranded")
    assert not any(key.startswith("KWS_") for key in os.environ)
    assert "APP_ENV" not in os.environ
    assert "DATABASE_URL" not in os.environ
    assert inventory.main(["--database-url", f"sqlite:///{path}"]) == 0
    output = capsys.readouterr().out
    assert "Complete consent evidence: 1" in output
    assert "Without complete consent evidence: 1" in output
    assert "under13_no_flow" in output
    assert "Runtime-policy eligibility: not evaluated" in output
    assert "Currently eligible" not in output
