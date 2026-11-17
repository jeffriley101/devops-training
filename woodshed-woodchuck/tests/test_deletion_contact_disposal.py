"""Exercise verified account cleanup against migrated disposable PostgreSQL."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models import PracticeChart, PracticeChartVerification, PracticeEmailPreset, TrustedVerifierInvitation
from app.security import hash_invitation_token
from app.verifier_invitation_disposal import REMOVED_INVITATION_EMAIL, unverified_adult_invitation_contact_count
from tests.test_account_deletion import profile
from tests.test_adult_deletion_support import adult_case
from tests.test_associated_disposal_support import apply_disposal_acl
from tests.test_historical_deletion_cleanup import prior_deletion
from tests.test_parent_deletion_support import support_case
from tests.test_prior_adult_retention import previous_adult


def invitation(student_id, adult_id, email, at, tag):
    return TrustedVerifierInvitation(profile_id=student_id, accepted_verifier_id=adult_id, email=email,
        role="verifier", status="accepted", token_hash=hash_invitation_token(tag),
        created_at=at - timedelta(days=4), accepted_at=at - timedelta(days=2),
        expires_at=at - timedelta(days=1), updated_at=at - timedelta(days=2))


@pytest.mark.parametrize("qualified_role", [False, True])
def test_adult_contact_requires_matching_mailbox_actor_and_acceptance(play_pg, adult_case, qualified_role):
    c = adult_case
    with play_pg.begin() as db:
        if qualified_role:
            apply_disposal_acl(db, "adult", c.target.role)
        child = profile(db, 1)
        verified = invitation(child.id, c.adult.id, "  " + c.adult.email.upper(), c.now, "verified")
        wrong_mailbox = invitation(child.id, c.adult.id, c.colleague.email, c.now, "other-address")
        wrong_actor = invitation(child.id, c.colleague.id, c.adult.email, c.now, "other-actor")
        bad_clock = invitation(child.id, c.adult.id, c.adult.email, c.now, "bad-clock")
        bad_clock.accepted_at = bad_clock.created_at - timedelta(hours=1)
        preset = PracticeEmailPreset(profile_id=child.id, display_name="Independently entered", email=c.adult.email)
        db.add_all([verified, wrong_mailbox, wrong_actor, bad_clock, preset])
    result = c.run()
    assert result["disposal"]["accepted_invitation_contacts"] == (
        "contact_review_required" if qualified_role else "access_required")
    assert c.run(status_only=True) == result
    with play_pg() as db:
        saved = db.get(TrustedVerifierInvitation, verified.id)
        assert saved.email == (REMOVED_INVITATION_EMAIL if qualified_role else verified.email)
        assert (saved.profile_id, saved.accepted_verifier_id, saved.created_at, saved.accepted_at,
                saved.expires_at, saved.updated_at) == (child.id, c.adult.id, verified.created_at, verified.accepted_at,
                                                       verified.expires_at, verified.updated_at)
        for original in (wrong_mailbox, wrong_actor, bad_clock):
            assert db.get(TrustedVerifierInvitation, original.id).email == original.email
        assert unverified_adult_invitation_contact_count(db, c.adult.id) == (2 if qualified_role else 3)
        assert db.get(PracticeEmailPreset, preset.id).email == c.adult.email


@pytest.mark.parametrize("rotated_key", [False, True])
def test_historical_adult_contact_checks_original_mailbox_key(play_pg, previous_adult, monkeypatch, rotated_key):
    c, case, _, clean = previous_adult
    original = c.register / (c.case["case"] + ".result.json")
    original_receipt = original.read_bytes()
    with play_pg.begin() as db:
        apply_disposal_acl(db, "adult", c.target.role)
        child = profile(db, 73)
        row = invitation(child.id, c.adult.id, c.adult.email, c.now, "old-contact")
        db.add(row)
    if rotated_key:
        monkeypatch.setenv("SESSION_SECRET", "synthetic-subsequent-signing-secret")
    result = clean()
    assert result["deleted_at"] == case["deleted_at"]
    assert original.read_bytes() == original_receipt
    with play_pg() as db:
        current = db.get(TrustedVerifierInvitation, row.id)
        assert current.email == (row.email if rotated_key else REMOVED_INVITATION_EMAIL)
        assert current.accepted_at == row.accepted_at and current.accepted_verifier_id == row.accepted_verifier_id
        if rotated_key:
            assert result["disposal"]["accepted_invitation_contacts"] == "contact_review_required"
            assert unverified_adult_invitation_contact_count(db, c.adult.id) >= 1


def test_historical_student_delivery_disposal_preserves_other_student_and_review_outcome(play_pg, prior_deletion):
    clean, _, case, other_id, _, chart_id, invitation_id, restricted_engine = prior_deletion
    deletion = datetime.fromisoformat(case["deleted_at"])
    delivery = deletion - timedelta(days=5)
    with play_pg.begin() as db:
        role = restricted_engine.url.username
        db.execute(text(f"GRANT UPDATE (ordinary_email_attempted_at,ordinary_email_sent_at) ON practice_charts TO {role}"))
        for table in ("practice_chart_verifications", "trusted_verifier_invitations"):
            db.execute(text(f"GRANT UPDATE (last_email_attempt_at,last_email_sent_at,email_attempt_count) ON {table} TO {role}"))
        chart = db.get(PracticeChart, chart_id)
        chart.ordinary_email_attempted_at = chart.ordinary_email_sent_at = delivery
        intake = db.get(TrustedVerifierInvitation, invitation_id)
        intake.last_email_sent_at = intake.last_email_attempt_at = delivery
        intake.email_attempt_count = 2
        other_chart = PracticeChart(profile_id=other_id, minutes=15, instrument="Flute", practice_date=delivery.date(),
                                    ordinary_email_attempted_at=delivery, ordinary_email_sent_at=delivery)
        db.add(other_chart)
        db.flush()
        reviews = []
        for owner in (chart, other_chart):
            review = PracticeChartVerification(practice_chart_id=owner.id, status="approved",
                requested_at=delivery, responded_at=delivery + timedelta(hours=1), updated_at=delivery + timedelta(hours=1),
                last_email_attempt_at=delivery, last_email_sent_at=delivery, email_attempt_count=2)
            db.add(review)
            reviews.append(review)
    result = clean()
    assert result["deleted_at"] == case["deleted_at"]
    assert result["disposal"]["delivery_history"] == "verified"
    with play_pg() as db:
        for row in (db.get(TrustedVerifierInvitation, intake.id), db.get(PracticeChartVerification, reviews[0].id)):
            assert (row.last_email_attempt_at, row.last_email_sent_at, row.email_attempt_count) == (None, None, 0)
        for owner, expected in ((chart, None), (other_chart, delivery)):
            saved = db.get(PracticeChart, owner.id)
            assert saved.ordinary_email_attempted_at == saved.ordinary_email_sent_at == expected
        kept = db.get(PracticeChartVerification, reviews[1].id)
        assert (kept.last_email_attempt_at, kept.last_email_sent_at, kept.email_attempt_count) == (delivery, delivery, 2)
        for original in reviews:
            saved = db.get(PracticeChartVerification, original.id)
            assert (saved.status, saved.requested_at, saved.responded_at, saved.updated_at) == (
                original.status, original.requested_at, original.responded_at, original.updated_at)
