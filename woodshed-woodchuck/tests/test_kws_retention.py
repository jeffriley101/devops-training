"""Focused retention tests; no KWS network calls or real identifiers."""
from datetime import date, datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import kws_retention
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence, DirectorPermission, PendingConsent
from app.db import Base
from app.kws_models import KWSEmailBudget, KWSVerification
from app.models import PracticeChart, TesterEnrollment as Enrollment, WoodchuckProfile
from app.security import hash_pin


NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


@pytest.fixture
def retention_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    engine.dispose()


def pending(session, tag, *, expires_at, profile_id=None):
    row = PendingConsent(
        profile_id=profile_id,
        parent_email=f"parent-{tag}@example.test",
        director_email="",
        director_name="",
        review_allowed=False,
        approve_hash=(tag * 64)[:64],
        created_at=NOW - timedelta(days=40),
        expires_at=expires_at,
        notice_version="synthetic-notice",
    )
    session.add(row)
    session.flush()
    return row


def verification(session, row, tag, state, *, completed_at=None, activated_consent_id=None):
    item = KWSVerification(
        pending_id=row.id,
        payload_hash=(tag.upper() * 64)[:64],
        environment="production",
        org_id="synthetic-org",
        product_id=None,
        binding_sha256=("b" + tag) * 32,
        notice_sha256=("n" + tag) * 32,
        profile_session_version=None,
        prior_consent_id=None,
        guardian_attested_at=NOW - timedelta(days=40),
        notice_accepted_at=NOW - timedelta(days=40),
        account_allowed=True,
        director_allowed=False,
        created_at=NOW - timedelta(days=40),
        expires_at=row.expires_at,
        state=state,
        completed_at=completed_at,
        activated_consent_id=activated_consent_id,
    )
    session.add(item)
    session.flush()
    return item


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_old_expired_failed_cancelled_nonactivated_flows_are_eligible(retention_db):
    with retention_db() as session:
        pending(session, "a", expires_at=NOW - timedelta(days=31))
        expired = pending(session, "b", expires_at=NOW - timedelta(days=31))
        verification(session, expired, "c", "accepted")
        failed = pending(session, "d", expires_at=NOW + timedelta(days=1))
        verification(session, failed, "e", "failed", completed_at=NOW - timedelta(days=31))
        cancelled = pending(session, "f", expires_at=NOW - timedelta(days=31))
        verification(session, cancelled, "g", "cancelled")
        verified = pending(session, "h", expires_at=NOW - timedelta(days=31))
        verification(session, verified, "i", "verified", completed_at=NOW - timedelta(days=34))
        session.commit()

        report = kws_retention.cleanup(session, now=NOW, apply=True)
        session.commit()

        assert report["pending_consents"] == 5
        assert report["kws_verifications"] == 4
        assert count(session, PendingConsent) == count(session, KWSVerification) == 0


def test_recent_current_and_still_valid_verified_flows_remain(retention_db):
    with retention_db() as session:
        recent = pending(session, "a", expires_at=NOW - timedelta(days=29))
        verification(session, recent, "b", "cancelled")
        current = pending(session, "c", expires_at=NOW + timedelta(hours=1))
        verification(session, current, "d", "accepted")
        failed = pending(session, "e", expires_at=NOW - timedelta(days=31))
        verification(session, failed, "f", "failed", completed_at=NOW - timedelta(days=29))
        verified = pending(session, "g", expires_at=NOW + timedelta(hours=72))
        verification(session, verified, "h", "verified", completed_at=NOW)
        session.commit()

        assert kws_retention.cleanup(session, now=NOW, apply=True)["pending_consents"] == 0
        session.commit()
        assert count(session, PendingConsent) == count(session, KWSVerification) == 4


def test_activated_consent_director_child_history_and_tester_enrollment_remain(retention_db):
    with retention_db() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-RETENTION-A", display_name="Synthetic Child",
            pin_hash=hash_pin("2468"), instrument="Flute", level="Beginner",
            goal="Practice every day",
        )
        session.add(profile)
        session.flush()
        evidence = ConsentEvidence(
            profile_id=profile.id, parent_email="parent@example.test",
            notice_version="synthetic-notice", notice_sha256="n" * 64,
            approved_at=NOW - timedelta(days=90), confirmed_at=NOW - timedelta(days=90),
        )
        session.add(evidence)
        session.flush()
        session.add_all([
            AccountPrivacy(profile_id=profile.id, age_band="under13", declared_at=NOW,
                           consent_id=evidence.id),
            DirectorPermission(profile_id=profile.id, consent_id=evidence.id,
                               email="director@example.test", director_name="Synthetic Director",
                               review_allowed=True, authorized_at=NOW - timedelta(days=90)),
            Enrollment(profile_id=profile.id, cohort_key="C001",
                       joined_at=NOW - timedelta(days=90)),
            PracticeChart(profile_id=profile.id, practice_date=date(2026, 9, 1), minutes=20,
                          instrument="Flute", note="retained", practice_details=[]),
        ])
        row = pending(session, "a", expires_at=NOW - timedelta(days=80), profile_id=profile.id)
        verification(session, row, "b", "activated", completed_at=NOW - timedelta(days=90),
                     activated_consent_id=evidence.id)
        session.commit()

        report = kws_retention.cleanup(session, now=NOW, apply=True)
        session.commit()

        assert report["pending_consents"] == report["kws_verifications"] == 0
        for model in (PendingConsent, KWSVerification, ConsentEvidence, DirectorPermission,
                      WoodchuckProfile, PracticeChart, Enrollment):
            assert count(session, model) == 1


def test_dry_run_changes_nothing_and_cli_prints_counts_only(retention_db, monkeypatch, capsys):
    with retention_db() as session:
        row = pending(session, "a", expires_at=NOW - timedelta(days=31))
        verification(session, row, "b", "cancelled")
        session.add(KWSEmailBudget(email_hash="sensitive-hash", sent_at=[NOW.timestamp() - 7200]))
        session.commit()
        before = (count(session, PendingConsent), count(session, KWSVerification),
                  count(session, KWSEmailBudget))
        report = kws_retention.cleanup(session, now=NOW, apply=False)
        assert report == {"pending_consents": 1, "kws_verifications": 1,
                          "email_budget_rows": 1, "email_budget_timestamps": 1}
        assert (count(session, PendingConsent), count(session, KWSVerification),
                count(session, KWSEmailBudget)) == before

    monkeypatch.setattr(kws_retention, "SessionLocal", retention_db)
    monkeypatch.setattr(kws_retention, "datetime", type("Clock", (), {
        "now": staticmethod(lambda tz: NOW),
    }))
    assert kws_retention.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert all(type(value) is int for value in json.loads(output).values())
    assert "sensitive-hash" not in output and "parent-a@example.test" not in output
    with retention_db() as session:
        assert count(session, PendingConsent) == count(session, KWSVerification) == 1


def test_repeated_cleanup_is_safe(retention_db):
    with retention_db() as session:
        row = pending(session, "a", expires_at=NOW - timedelta(days=31))
        verification(session, row, "b", "cancelled")
        session.add(KWSEmailBudget(email_hash="old", sent_at=[NOW.timestamp() - 7200]))
        session.commit()
        first = kws_retention.cleanup(session, now=NOW, apply=True)
        session.commit()
        second = kws_retention.cleanup(session, now=NOW, apply=True)
        session.commit()
        assert first == {"pending_consents": 1, "kws_verifications": 1,
                         "email_budget_rows": 1, "email_budget_timestamps": 1}
        assert second == {key: 0 for key in first}


@pytest.mark.parametrize("history", [
    lambda now: [now.timestamp() - 7200, now.timestamp() - 60],
    lambda now: [now.timestamp() - 7200, now.timestamp() - 3500,
                 now.timestamp() - 1800, now.timestamp() - 301],
])
def test_cleanup_cannot_bypass_five_minute_or_hourly_email_limits(
        retention_db, monkeypatch, history):
    from app import kws_verification

    monkeypatch.setattr(kws_verification.consent, "clock", lambda: NOW)
    monkeypatch.setenv("SESSION_SECRET", "synthetic-retention-session-secret")
    with retention_db() as session:
        kws_verification.reserve_budget(session, "rate-limit@example.test", "production")
        budget = session.scalar(select(KWSEmailBudget))
        budget.sent_at = history(NOW)
        session.commit()

        report = kws_retention.cleanup(session, now=NOW, apply=True)
        session.commit()
        assert report["email_budget_rows"] == report["email_budget_timestamps"] == 1
        with pytest.raises(ValueError, match="Wait before requesting"):
            kws_verification.reserve_budget(session, "rate-limit@example.test", "production")
