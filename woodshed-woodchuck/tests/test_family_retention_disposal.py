"""Scope and original-clock qualification using disposable PostgreSQL."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import child_authorization, kws_retention, kws_verification
from app.child_models import ConsentEvidence, DirectorPermission, ParentAccess, PendingConsent
from app.main import app
from app.kws_models import KWSVerification
from app.models import TrustedVerifierInvitation, WoodchuckProfile
from app.security import hash_invitation_token
from app.verifier_invitation_disposal import clear_closed_student_invitations, closed_student_invitations_complete
from tests.test_account_deletion import NOW, profile
from tests.test_kws_retention import pending, verification
from tests.test_play_deletion_cleanup import access_rows, invitation_fields


def credentials(row):
    return row.link_hash, row.link_expires_at, row.session_hash, row.session_expires_at


@pytest.mark.parametrize("consent_visibility", ("no_grant", "rls"))
def test_kws_role_without_full_consent_visibility_holds_only_ambiguous_requests(play_pg, consent_visibility):
    role = "legacy_retention_" + uuid4().hex
    with play_pg.begin() as session:
        unverified = pending(session, "n", expires_at=NOW - timedelta(days=31))
        answered = pending(session, "r", expires_at=NOW - timedelta(days=31))
        verification(session, answered, "r", "cancelled")
        session.execute(text(f"CREATE ROLE {role} LOGIN"))
        for table, key in (("child_pending_consents", "id"), ("child_kws_verifications", "id"),
                           ("child_kws_email_budgets", "email_hash")):
            session.execute(text(f"GRANT SELECT, DELETE, UPDATE ({key}) ON public.{table} TO {role}"))
        if consent_visibility == "rls":
            session.execute(text(f"GRANT SELECT (profile_id, parent_email) ON child_consent_evidence TO {role}"))
            session.execute(text("ALTER TABLE child_consent_evidence ENABLE ROW LEVEL SECURITY"))
    engine = create_engine(play_pg.kw["bind"].url.set(username=role))
    try:
        with Session(engine) as session, session.begin():
            result = kws_retention.cleanup(session, now=NOW, apply=True)
            assert result["unresolved_legacy_requests"] == 1
            assert result["pending_consents"] == result["kws_verifications"] == 1
        with play_pg() as session:
            assert session.get(PendingConsent, unverified.id)
            assert session.get(PendingConsent, answered.id) is None
    finally:
        engine.dispose()
        with play_pg.begin() as session:
            session.execute(text(f"DROP OWNED BY {role}"))
            session.execute(text(f"DROP ROLE {role}"))


def test_repeated_withdrawal_preserves_nonactivated_kws_disposal_deadlines(play_pg, monkeypatch):
    first = NOW - timedelta(days=29)
    with play_pg.begin() as session:
        open_flow = pending(session, "a", expires_at=first + timedelta(hours=24))
        verification(session, open_flow, "a", "accepted")
        failed = pending(session, "b", expires_at=NOW + timedelta(hours=24))
        verification(session, failed, "b", "failed", completed_at=first)
        unknown = pending(session, "c", expires_at=first)
        item = verification(session, unknown, "c", "unknown")
        payload = "c" * 43
        item.payload_hash = hash_invitation_token(payload)
    for at in (first, NOW):
        monkeypatch.setattr(child_authorization, "clock", lambda: at)
        with play_pg.begin() as session:
            for row in (open_flow, failed, unknown):
                kws_verification.cancel(session, session.get(PendingConsent, row.id))
    with play_pg.begin() as session:
        monkeypatch.setattr(child_authorization, "require_under13_review", lambda: None)
        with pytest.raises(ValueError, match="permission request is unavailable"):
            kws_verification.deliver(session, item.id, payload)
        config = SimpleNamespace(environment=item.environment, org_id=item.org_id, product_id=item.product_id)
        with pytest.raises(kws_verification.InvalidResult) as error:
            kws_verification.complete(session, config, payload, {"verified": True, "transactionId": "synthetic"},
                                      NOW.timestamp())
        assert error.value.stage == kws_verification.RejectionStage.COMPLETION_REJECTED
        assert session.get(PendingConsent, open_flow.id).expires_at == first
        assert kws_retention.cleanup(session, now=NOW, apply=True)["pending_consents"] == 2
    with play_pg() as session:
        assert session.get(PendingConsent, unknown.id) is not None
        assert session.get(KWSVerification, item.id).state == "unknown"


def test_withdrawal_of_previously_deleted_account_disposes_only_covered_private_credentials(play_pg, monkeypatch):
    deletion_at = NOW - timedelta(days=50)
    withdrawn_at = NOW + timedelta(days=1)
    monkeypatch.setattr(child_authorization, "clock", lambda: withdrawn_at)
    with play_pg.begin() as session:
        student, sibling = profile(session, 1), profile(session, 2)
        consent, parent, director = access_rows(session, student, "former")
        other_consent, other_parent, other_director = access_rows(session, sibling, "current")
        request = PendingConsent(profile_id=student.id, parent_email=consent.parent_email, director_email="",
            director_name="", notice_version=consent.notice_version, created_at=deletion_at - timedelta(days=5),
            approve_hash=hash_invitation_token("old-approved-request"), expires_at=deletion_at)
        session.add(request)
        session.flush()
        token = child_authorization.derived_token(request, "withdraw")
        consent.withdrawal_hash = hash_invitation_token(token)
        # Synthetic legacy case with independent family authority and surviving credential rows.
        student.status, student.deleted_at = "deleted", deletion_at
        other_credentials = credentials(other_parent), credentials(other_director)
    with TestClient(app) as client:
        # The earlier request still supplies withdrawal identity even without a
        # KWS row, including when an expired unanswered request can be removed.
        with play_pg.begin() as session:
            unanswered = pending(session, "u", expires_at=deletion_at)
            result = kws_retention.cleanup(session, now=withdrawn_at, apply=True)
            assert result["pending_consents"] == result["unresolved_legacy_requests"] == 1
        with play_pg() as session:
            assert session.get(PendingConsent, unanswered.id) is None
            assert session.get(PendingConsent, request.id) is not None
        path = f"/family/withdraw/{token}"
        form = client.get(path)
        assert client.post(path, data={"csrf": form.context["csrf"], "withdraw": "yes"}).status_code == 200
    monkeypatch.setattr(child_authorization, "clock", lambda: withdrawn_at + timedelta(days=2))
    with play_pg.begin() as session:
        child_authorization.withdraw_evidence(session, session.get(ConsentEvidence, consent.id))
    with play_pg() as session:
        assert session.get(WoodchuckProfile, student.id).deleted_at == deletion_at
        assert session.get(ConsentEvidence, consent.id).withdrawn_at == withdrawn_at
        assert session.get(ConsentEvidence, other_consent.id).withdrawn_at is None
        assert credentials(session.get(ParentAccess, parent.consent_id)) == (None,) * 4
        assert credentials(session.get(DirectorPermission, director.id)) == (None,) * 4
        assert credentials(session.get(ParentAccess, other_parent.consent_id)) == other_credentials[0]
        assert credentials(session.get(DirectorPermission, other_director.id)) == other_credentials[1]


def test_director_revocation_preserves_original_end_and_independent_parent_access(play_pg, monkeypatch):
    with play_pg.begin() as session:
        student = profile(session, 3)
        consent, parent, director = access_rows(session, student, "director")
        parent_credentials = credentials(parent)
    for at in (NOW, NOW + timedelta(days=40)):
        monkeypatch.setattr(child_authorization, "clock", lambda: at)
        with play_pg.begin() as session:
            child_authorization.revoke_director_permission(session, session.get(DirectorPermission, director.id))
    with play_pg() as session:
        saved = session.get(DirectorPermission, director.id)
        assert saved.revoked_at == NOW and credentials(saved) == (None,) * 4
        assert (saved.email, saved.director_name, saved.authorized_at) == (
            director.email, director.director_name, director.authorized_at)
        assert credentials(session.get(ParentAccess, parent.consent_id)) == parent_credentials
        assert session.get(ConsentEvidence, consent.id).withdrawn_at is None


def test_closed_invitation_cleanup_only_treats_known_consistent_rows_and_can_repeat(play_pg):
    with play_pg.begin() as session:
        student, sibling = profile(session, 4), profile(session, 5)
        student.status, student.deleted_at = "deleted", NOW - timedelta(days=40)
        def invitation(target, status, tag, *, accepted_at=None):
            return TrustedVerifierInvitation(profile_id=target.id, email="shared@example.test",
                role="verifier", status=status, accepted_at=accepted_at,
                expires_at=NOW - timedelta(days=60), updated_at=NOW - timedelta(days=50),
                token_hash=hash_invitation_token(tag))
        eligible = invitation(student, "cancelled", "closed-credential")
        untouched = [invitation(student, "cancelled", "conflicting-actor-event", accepted_at=NOW),
                     invitation(student, "accepted", "unknown-actor", accepted_at=NOW),
                     invitation(student, "legacy_closed", "unknown-state"),
                     invitation(sibling, "cancelled", "another-student")]
        session.add_all([eligible, *untouched])
        session.flush()
        original = [invitation_fields(row) for row in untouched]
        expected = {**invitation_fields(eligible), "email": "deleted@invalid.local",
                    "token_hash": f"retired:{eligible.id}"}
    for _ in range(2):
        with play_pg.begin() as session:
            clear_closed_student_invitations(session, student.id)
            assert closed_student_invitations_complete(session, student.id)
    with play_pg() as session:
        assert invitation_fields(session.get(TrustedVerifierInvitation, eligible.id)) == expected
        assert [invitation_fields(session.get(TrustedVerifierInvitation, row.id)) for row in untouched] == original
