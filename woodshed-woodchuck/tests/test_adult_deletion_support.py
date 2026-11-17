"""Verified adult Support fulfillment against a newly migrated private PostgreSQL cluster."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import child_authorization, classroom, classroom_invitation, parent_access, verifiers
from app.adult_account_deletion import AdultDeletionHold
from app.age_privacy import declare_age
from app.child_models import ConsentEvidence, DirectorPermission, ParentAccess
from app.classroom_invitation import create_program_invitation
from app.classroom_models import (
    ClassroomAuditEvent, ClassroomClass, ClassroomOwnershipTransfer, ClassroomProgram,
    ClassroomRoleGrant, ClassroomStudentMembership, ClassroomTeachingAssignment,
)
from app.main import app
from app.membership_models import BillingAccount, CheckoutAttempt, Membership, MembershipSeat, ProviderSubscription
from app.models import (
    Organization, PracticeChart, PracticeChartVerification, StudentVerifierConnection, TrustedVerifier,
    TrustedVerifierInvitation, WoodchuckProfile, WoodchuckState,
)
from app.parent_deletion_support import ACTION, ADULT_ACTION, SupportDenied, SupportTarget, fulfill
from app.security import hash_invitation_token, hash_pin, verify_pin
from app.session_revocations import RevokedBrowserSession, purge_expired
from app.verifier_routes import SESSION_VERIFIER_ID, current_verifier
from app.verifiers import (
    accept_trusted_verifier_invitation, create_trusted_verifier_invitation, reissue_trusted_verifier_invitation,
)
from tests.test_account_deletion import NOW, profile
from tests.test_play_deletion_cleanup import access_rows


@pytest.fixture
def adult_case(play_pg, tmp_path):
    now = datetime.now(timezone.utc) - timedelta(seconds=3)
    role = "adult_support_" + uuid4().hex
    with play_pg.begin() as session:
        adults = [TrustedVerifier(email=email, display_name=name, pin_hash=hash_pin(pin),
                                  created_at=now - timedelta(days=3))
                  for email, name, pin in (("director@example.test", "Synthetic Director", "1357"),
                                           ("colleague@example.test", "Other adult", "2468"))]
        session.add_all(adults)
        session.flush()
        session.execute(text(f"CREATE ROLE {role} LOGIN"))
        for table in ("alembic_version", "trusted_verifiers", "trusted_verifier_invitations", "revoked_browser_sessions"):
            session.execute(text(f"GRANT SELECT ON public.{table} TO {role}"))
        readable = {
            "student_verifier_connections": "id, verifier_id, status",
            "child_director_permissions": "id, profile_id, consent_id, connection_id, email, revoked_at, "
                                          "link_hash, link_expires_at, session_hash, session_expires_at",
            "child_consent_evidence": "id, withdrawn_at",
            "child_pending_consents": "id, director_email, expires_at",
            "child_kws_verifications": "pending_id, director_allowed, state",
            "account_privacy_rules": "profile_id, consent_id",
            "woodchuck_profiles": "id, status",
            "classroom_programs": "organization_id, owner_verifier_id",
            "billing_accounts": "id, verifier_id",
            "memberships": "id, billing_account_id, status, revoked_at, access_until",
            "membership_seats": "id, membership_id, removed_at",
            "membership_seat_invitations": "id, membership_id, expires_at, status",
            "provider_subscriptions": "id, membership_id, provider, external_subscription_id, terminated_at",
            "checkout_attempts": "id, billing_account_id, reference, status",
            "billing_event_applications": "id, event_id, checkout_reference, external_subscription_id, status",
            "billing_provider_events": "id, provider",
        }
        for table, columns in readable.items():
            session.execute(text(f"GRANT SELECT ({columns}) ON public.{table} TO {role}"))
        writable = {
            "trusted_verifiers": "email, display_name, pin_hash, updated_at",
            "student_verifier_connections": "status, updated_at",
            "child_director_permissions": "link_hash, link_expires_at, session_hash, session_expires_at",
            "trusted_verifier_invitations": "token_hash, updated_at",
            "revoked_browser_sessions": "revoked_at, expires_at",
            "billing_accounts": "updated_at",
        }
        for table, columns in writable.items():
            session.execute(text(f"GRANT UPDATE ({columns}) ON public.{table} TO {role}"))
        session.execute(text(f"GRANT INSERT ON public.revoked_browser_sessions TO {role}"))
        database, oid = session.execute(text(
            "SELECT datname::text, oid::bigint FROM pg_database WHERE datname = current_database()"
        )).one()
    adult, colleague = adults
    target = SupportTarget(database, oid, role)
    register, approvals = tmp_path / "register", tmp_path / "approvals"
    for path in (register, approvals):
        path.mkdir(mode=0o700)
    reference = "adult-case-001"
    (approvals / (reference + ".lock")).touch(mode=0o600)
    case = {
        "case": reference, "status": "approved", "verifier_id": adult.id,
        "email": adult.email, "verifier_created_at": adult.created_at.isoformat(),
        "requester_ref": "requester-01", "identity_ref": "identity-01", "authority_ref": "mailbox-01",
        "verified_by": "analyst-0001", "verified_at": (now - timedelta(minutes=10)).isoformat(),
        "disputed": True, "reviewed_by": "reviewer-0002",
        "confirmation": {
            "case": reference, "verifier_id": adult.id, "requester_ref": "requester-01",
            "action": ADULT_ACTION, "reference": "decision-01", "at": (now - timedelta(minutes=5)).isoformat(),
        },
    }

    def publish(record):
        path = approvals / (reference + ".json")
        path.write_text(json.dumps(record))
        path.chmod(0o600)

    publish(case)
    engine = create_engine(play_pg.kw["bind"].url.set(username=role))
    factory = sessionmaker(bind=engine, autoflush=False)

    def run(**overrides):
        parameters = dict(factory=factory, target=target, approval_directory=approvals, approval_uid=os.geteuid(),
                          register=register, reference=reference, clock=lambda: now, adult=True)
        return fulfill(**(parameters | overrides))

    try:
        yield SimpleNamespace(run=run, publish=publish, case=case, adult=adult, colleague=colleague,
                              register=register, target=target, engine=engine, now=now)
    finally:
        engine.dispose()
        with play_pg.begin() as session:
            session.execute(text(f"DROP OWNED BY {role}"))
            session.execute(text(f"DROP ROLE {role}"))


def records(factory, *models):
    with factory() as session:
        return {model: session.execute(select(model.__table__).order_by(*model.__table__.primary_key.columns)).all()
                for model in models}


def login(adult, pin):
    client = TestClient(app)
    response = client.post("/trusted-verifiers/login", data={"email": adult.email, "pin": pin})
    assert response.status_code == 200 and response.json()["verifier"]["id"] == adult.id
    return client


def test_verified_verifier_and_director_preserves_student_history_and_revokes_old_sessions(play_pg, adult_case):
    case = adult_case
    with play_pg.begin() as session:
        students = [profile(session, n) for n in (1, 2)]
        for student, role in zip(students, ("band_director", "verifier")):
            declare_age(session, student.id, "13to17")
            session.add(StudentVerifierConnection(profile_id=student.id, verifier_id=case.adult.id,
                        role=role, status="accepted", accepted_at=case.now - timedelta(days=2)))
        shared = StudentVerifierConnection(profile_id=students[0].id, verifier_id=case.colleague.id,
                    role="verifier", status="accepted", accepted_at=case.now - timedelta(days=1))
        session.add(shared)
        chart = PracticeChart(profile_id=students[0].id, practice_date=case.now.date(), minutes=25,
                              instrument="Flute", note="Student's original chart")
        session.add(chart)
        session.flush()
        session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=case.adult.id,
                    status="approved", response_note="Original review", responded_at=case.now - timedelta(days=1)))
        invitation = TrustedVerifierInvitation(profile_id=students[0].id, email=case.adult.email,
                        role="band_director", status="accepted", token_hash=hash_invitation_token("used-historical"),
                        accepted_verifier_id=case.adult.id, accepted_at=case.now - timedelta(days=2),
                        expires_at=case.now - timedelta(days=1))
        session.add(invitation)
    preserved = records(play_pg, WoodchuckProfile, WoodchuckState, PracticeChart, PracticeChartVerification,
                        TrustedVerifierInvitation)
    original = login(case.adult, "1357")
    colleague = login(case.colleague, "2468")
    signed_cookie = original.cookies.get("session")
    csv = original.get("/band-director/dashboard.csv", follow_redirects=False)
    assert csv.status_code == 200 and students[0].woodchuck_id in csv.text
    assert original.get("/membership", follow_redirects=False).status_code == 200
    assert case.run(status_only=True)["status"] == "no_recorded_execution"
    receipt = case.run()
    assert receipt["status"] == "adult_deletion_applied"
    assert case.run() == case.run(status_only=True) == receipt
    for url, location in (("/band-director/dashboard.csv", "/trusted-verifiers/login"), ("/membership", "/login")):
        replay = TestClient(app)
        replay.cookies.set("session", signed_cookie)
        response = replay.get(url, follow_redirects=False)
        assert (response.status_code, response.headers.get("location")) == (303, location)
    assert original.get("/trusted-verifiers/me").json()["authenticated"] is False
    assert original.post("/trusted-verifiers/login", data={"email": case.adult.email, "pin": "1357"}).status_code == 401
    assert colleague.get("/trusted-verifiers/me").json()["verifier"]["id"] == case.colleague.id
    with play_pg() as session:
        deleted = session.get(TrustedVerifier, case.adult.id)
        assert deleted.email != case.adult.email and deleted.display_name != case.adult.display_name
        assert not verify_pin("1357", deleted.pin_hash)
        assert set(session.scalars(select(StudentVerifierConnection.status).where(
            StudentVerifierConnection.verifier_id == deleted.id))) == {"disconnected"}
        assert session.get(StudentVerifierConnection, shared.id).status == "accepted"
    assert records(play_pg, *preserved) == preserved
    for entry in case.register.glob("*.json"):
        for private in (case.adult.email, case.adult.display_name, "requester-01", "identity-01", "mailbox-01",
                        "decision-01", case.adult.pin_hash):
            assert private not in entry.read_text()


def test_approval_requires_verified_identity_confirmation_and_bound_executor(play_pg, adult_case):
    c = adult_case
    case, conf = c.case, c.case["confirmation"]
    with c.engine.connect() as executor:
        permissions = executor.execute(text(
            "SELECT has_column_privilege('child_consent_evidence', 'parent_email', 'SELECT'), "
            "has_column_privilege('practice_chart_verifications', 'verifier_id', 'UPDATE'), "
            "has_column_privilege('classroom_programs', 'owner_verifier_id', 'UPDATE'), "
            "has_table_privilege('woodchuck_profiles', 'DELETE')"
        )).one()
        assert permissions == (False, False, False, False)
    with pytest.raises(SupportDenied, match="support_target_mismatch"):
        c.run(target=replace(c.target, database_oid=c.target.database_oid + 1))
    with pytest.raises(SupportDenied, match="support_nonadministrative_role_required"):
        c.run(factory=play_pg, target=replace(c.target, role=play_pg.kw["bind"].url.username))
    with pytest.raises(SupportDenied, match="support_case_invalid"):
        c.run(adult=False)
    attacker_writable = c.register.parent / "writable-parent"
    attacker_writable.mkdir()
    attacker_writable.chmod(0o777)
    unsafe_register = attacker_writable / "private-register"
    unsafe_register.mkdir(mode=0o700)
    with pytest.raises(SupportDenied, match="support_private_path_required"):
        c.run(register=unsafe_register)
    invalid = (
        (case | {"confirmation": conf | {"verifier_id": c.colleague.id}}, "support_confirmation_mismatch"),
        (case | {"confirmation": conf | {"action": ACTION}}, "support_confirmation_mismatch"),
        (case | {"verifier_id": c.colleague.id, "confirmation": conf | {"verifier_id": c.colleague.id}},
         "support_adult_mismatch_or_unavailable"),
        (case | {"email": c.colleague.email}, "support_adult_mismatch_or_unavailable"),
        (case | {"verifier_created_at": (c.now - timedelta(days=5)).isoformat()}, "support_adult_mismatch_or_unavailable"),
        (case | {"status": "revoked"}, "support_case_invalid"),
        (case | {"verified_at": (c.now - timedelta(days=2)).isoformat()}, "support_case_expired"),
        (case | {"reviewed_by": None}, "support_independent_review_required"),
        (case | {"verified_by": case["requester_ref"]}, "support_independent_review_required"),
        (case | {"authority_ref": case["identity_ref"]}, "support_distinct_evidence_required"),
    )
    original = records(play_pg, TrustedVerifier)
    for invalid_record, reason in invalid:
        c.publish(invalid_record)
        with pytest.raises(SupportDenied, match=reason):
            c.run()
    assert records(play_pg, TrustedVerifier) == original
    assert not list(c.register.glob("*.json"))


@pytest.mark.parametrize("entry", ["verifier_session", "legacy_program_invitation"])
def test_request_waits_for_deletion_and_observes_revocation(play_pg, adult_case, entry):
    c = adult_case
    before_commit = Event()
    request = SimpleNamespace(session={SESSION_VERIFIER_ID: c.adult.id})
    if entry == "legacy_program_invitation":
        with play_pg.begin() as setup:
            setup.get(TrustedVerifier, c.adult.id).email = " Director@Example.Test "
            school = Organization(name="Unprovisioned School", organization_type="school")
            setup.add(school)
            setup.flush()
            token = create_program_invitation(school.id, c.adult.email, now=c.now - timedelta(minutes=1))
    with play_pg() as request_db, ThreadPoolExecutor(max_workers=1) as worker:
        cached = request_db.get(TrustedVerifier, c.adult.id)
        assert cached.email.strip().lower() == c.adult.email
        request_pid = request_db.scalar(text("SELECT pg_backend_pid()"))

        class CommitWhenLookupIsWaiting(Session):
            def commit(self):
                # Release the deletion only after PostgreSQL shows it blocking this exact request.
                before_commit.set()
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    if self.scalar(text("SELECT pg_backend_pid() = ANY(pg_blocking_pids(:request_pid))"),
                                   {"request_pid": request_pid}):
                        break
                    sleep(0.01)
                else:
                    pytest.fail("The request did not wait on the deletion transaction.")
                super().commit()

        factory = sessionmaker(bind=c.engine, class_=CommitWhenLookupIsWaiting, autoflush=False)
        deletion = worker.submit(c.run, factory=factory)
        assert before_commit.wait(timeout=10)
        if entry == "verifier_session":
            assert current_verifier(request, request_db) is None
            assert SESSION_VERIFIER_ID not in request.session
        else:
            with pytest.raises(classroom.ClassroomDenied, match="invitation unavailable"):
                classroom.accept_program_invitation(request_db, token=token, pin="1357", display_name="Replacement")
            assert request_db.get(ClassroomProgram, school.id) is None
        assert deletion.result(timeout=10)["status"] == "adult_deletion_applied"


@pytest.mark.parametrize("duplicate", [True, False])
def test_canonical_legacy_mailbox_requires_one_exact_adult(play_pg, adult_case, duplicate):
    c = adult_case
    with play_pg.begin() as session:
        session.get(TrustedVerifier, c.adult.id).email = "  Director@Example.Test  "
        if duplicate:
            session.get(TrustedVerifier, c.colleague.id).email = c.case["email"]
    if duplicate:
        subjects = records(play_pg, TrustedVerifier)
        with pytest.raises(AdultDeletionHold, match="adult_mailbox_ambiguous"):
            c.run()
        assert records(play_pg, *subjects) == subjects
        assert not list(c.register.glob("*.json"))
    else:
        assert c.run()["status"] == "adult_deletion_applied"
        with play_pg() as session:
            assert session.get(TrustedVerifier, c.adult.id).email != "  Director@Example.Test  "
            assert session.get(TrustedVerifier, c.colleague.id).email == c.colleague.email


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_commit_rereads_actual_adult_state(play_pg, adult_case, committed):
    class Interrupted(Session):
        def commit(self):
            if committed:
                super().commit()
            raise RuntimeError("synthetic interrupted commit")

    c = adult_case
    with pytest.raises(RuntimeError, match="synthetic interrupted commit"):
        c.run(factory=sessionmaker(bind=c.engine, class_=Interrupted, autoflush=False))
    assert list(c.register.glob("*.intent.json")) and not list(c.register.glob("*.result.json"))
    if committed:
        assert c.run(status_only=True)["status"] == "adult_deletion_applied"
        with pytest.raises(SupportDenied, match="support_record_mismatch"):
            c.run(adult=False)
    else:
        with pytest.raises(SupportDenied, match="support_execution_unreconciled"):
            c.run(status_only=True)
        assert not list(c.register.glob("*.result.json"))
        with play_pg() as session:
            assert session.get(TrustedVerifier, c.adult.id).pin_hash == c.adult.pin_hash


@pytest.mark.parametrize("residual", ["live_connection", "lost_invitation_cutoff"])
def test_status_withholds_receipt_if_committed_revocation_is_incomplete(play_pg, adult_case, residual):
    c = adult_case
    with play_pg.begin() as session:
        student = profile(session, 9)
        connection = StudentVerifierConnection(profile_id=student.id, verifier_id=c.adult.id, role="verifier",
                                              status="accepted", accepted_at=c.now)
        session.add(connection)

    class IncompleteRevocation(Session):
        def commit(self):
            super().commit()
            with play_pg.begin() as separate:
                if residual == "live_connection":
                    separate.get(StudentVerifierConnection, connection.id).status = "accepted"
                else:
                    cutoff = separate.execute(select(RevokedBrowserSession)).scalar_one()
                    separate.delete(cutoff)

    with pytest.raises(SupportDenied, match="support_ordinary_cleanup_incomplete"):
        c.run(factory=sessionmaker(bind=c.engine, class_=IncompleteRevocation, autoflush=False))
    with pytest.raises(SupportDenied, match="support_ordinary_cleanup_incomplete"):
        c.run(status_only=True)
    assert list(c.register.glob("*.intent.json")) and not list(c.register.glob("*.result.json"))


def test_school_owner_can_complete_after_existing_transfer_preserving_school_permissions(play_pg, adult_case, monkeypatch):
    c = adult_case
    monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
    with play_pg.begin() as session:
        org = Organization(name="Synthetic School", organization_type="school", created_by_verifier_id=c.adult.id)
        session.add(org)
        session.flush()
        token = create_program_invitation(org.id, c.adult.email)
        classroom.accept_program_invitation(session, token=token, pin="1357")
    with play_pg.begin() as session:
        grade = ClassroomClass(program_id=org.id, display_name="Synthetic Class")
        student = profile(session, 3)
        session.add(grade)
        session.flush()
        session.add_all([
            ClassroomRoleGrant(program_id=org.id, verifier_id=c.colleague.id, granted_by_verifier_id=c.adult.id,
                               role="admin", starts_at=c.now),
            ClassroomTeachingAssignment(program_id=org.id, class_id=grade.id, verifier_id=c.colleague.id,
                                         granted_by_verifier_id=c.adult.id, starts_at=c.now),
            ClassroomStudentMembership(class_id=grade.id, profile_id=student.id),
        ])
    protected = records(play_pg, TrustedVerifier, Organization, ClassroomProgram, ClassroomRoleGrant,
                        ClassroomClass, ClassroomTeachingAssignment, ClassroomStudentMembership, ClassroomAuditEvent)
    with pytest.raises(AdultDeletionHold, match="adult_program_transfer_required"):
        c.run()
    assert records(play_pg, *protected) == protected
    assert not list(c.register.glob("*.json"))
    with play_pg.begin() as session:
        owner = classroom.authenticate_adult(session, email=c.adult.email, pin="1357")
        proposal = classroom.propose_transfer(session, actor=owner, program_id=org.id, recipient_id=c.colleague.id)
    with pytest.raises(AdultDeletionHold, match="adult_program_transfer_required"):
        c.run()
    with play_pg.begin() as session:
        recipient = classroom.authenticate_adult(session, email=c.colleague.email, pin="2468")
        classroom.accept_transfer(session, actor=recipient, program_id=org.id, transfer_id=proposal.id)
    school = records(play_pg, Organization, ClassroomProgram, ClassroomClass, ClassroomStudentMembership,
                     ClassroomTeachingAssignment, ClassroomOwnershipTransfer, ClassroomRoleGrant, ClassroomAuditEvent)
    assert c.run()["status"] == "adult_deletion_applied"
    assert records(play_pg, *school) == school
    with play_pg() as session:
        assert session.get(ClassroomProgram, org.id).owner_verifier_id == c.colleague.id
        with pytest.raises(classroom.ClassroomDenied):
            classroom.authenticate_adult(session, email=c.adult.email, pin="1357")
        colleague = classroom.authenticate_adult(session, email=c.colleague.email, pin="2468")
        assert classroom.my_classes(session, actor=colleague, program_id=org.id)["programs"]


@pytest.mark.parametrize("pending", ["checkout", "subscription", "seat"])
def test_billing_dependencies_hold_without_altering_other_users_or_financial_evidence(play_pg, adult_case, pending):
    c = adult_case
    financial = dict(provider="stripe", plan_code="synthetic", amount_cents=150, currency="usd", interval="month")
    with play_pg.begin() as session:
        account = BillingAccount(verifier_id=c.adult.id)
        student = profile(session, 4)
        session.add(account)
        session.flush()
        membership = Membership(billing_account_id=account.id, status="active" if pending == "seat" else "ended",
                    source="stripe", starts_at=c.now - timedelta(days=30),
                    access_until=c.now + timedelta(days=3) if pending == "seat" else c.now - timedelta(days=3))
        session.add(membership)
        session.flush()
        seat = MembershipSeat(membership_id=membership.id, profile_id=student.id, slot_number=1,
                              removed_at=None if pending == "seat" else c.now - timedelta(days=3))
        subscription = ProviderSubscription(membership_id=membership.id, external_subscription_id="sub-synthetic",
                        external_customer_id="cus-synthetic", provider_status="active" if pending == "subscription" else "canceled",
                        terminated_at=None if pending == "subscription" else c.now - timedelta(days=3), **financial)
        checkout = CheckoutAttempt(billing_account_id=account.id, reference="synthetic-checkout",
                    status="recoverable" if pending == "checkout" else "completed", expires_at=c.now + timedelta(hours=1),
                    **financial)
        session.add_all([seat, subscription, checkout])
    before = records(play_pg, BillingAccount, Membership, MembershipSeat, CheckoutAttempt, ProviderSubscription,
                     WoodchuckProfile, WoodchuckState, TrustedVerifier)
    with pytest.raises(AdultDeletionHold, match="adult_billing_reconciliation_required"):
        c.run()
    assert records(play_pg, *before) == before
    assert not list(c.register.glob("*.json"))
    # Advance the synthetic fixture to a separately confirmed terminal billing state.
    with play_pg.begin() as session:
        session.get(CheckoutAttempt, checkout.id).status = "expired"
        session.get(Membership, membership.id).status = "ended"
        session.get(Membership, membership.id).access_until = c.now
        session.get(MembershipSeat, seat.id).removed_at = c.now
        subscription = session.get(ProviderSubscription, subscription.id)
        subscription.provider_status, subscription.terminated_at = "canceled", c.now
    ended = records(play_pg, BillingAccount, Membership, MembershipSeat, CheckoutAttempt, ProviderSubscription)
    assert c.run()["status"] == "adult_deletion_applied"
    assert records(play_pg, *ended) == ended


def test_bound_private_director_loses_credentials_independent_guardian_keeps_access(play_pg, adult_case, monkeypatch):
    c = adult_case
    monkeypatch.setattr(child_authorization, "under13_available", lambda: True)
    monkeypatch.setattr(child_authorization, "clock", lambda: NOW)
    monkeypatch.setattr(child_authorization, "notice_policy", lambda environment=None: ("synthetic", "", "a" * 64))
    with play_pg.begin() as session:
        student = profile(session, 5)
        consent, parent, director = access_rows(session, student, "family", verifier_id=c.colleague.id)
        conn = StudentVerifierConnection(profile_id=student.id, verifier_id=c.adult.id, role="band_director",
                                        status="accepted", accepted_at=NOW)
        session.add(conn)
        session.flush()
        director.email, director.connection_id, director.review_allowed = c.adult.email, conn.id, True
    parent_session = SimpleNamespace(session={"child_parent_consent": consent.id, "child_parent_secret": "family-parent-session"})
    director_session = SimpleNamespace(session={"child_director_permission": director.id,
                                                "child_director_secret": "family-director-session"})
    with play_pg() as session:
        assert parent_access.authenticated(session, parent_session)[0].id == consent.id
        assert child_authorization.director_authenticated(session, director_session, review=True).id == director.id
    family = records(play_pg, WoodchuckProfile, WoodchuckState, ConsentEvidence, ParentAccess)
    assert c.run()["status"] == "adult_deletion_applied"
    assert records(play_pg, *family) == family
    with play_pg() as session:
        permission = session.get(DirectorPermission, director.id)
        assert (permission.link_hash, permission.session_hash, permission.link_expires_at, permission.session_expires_at) == (None,) * 4
        assert (permission.connection_id, permission.review_allowed, permission.revoked_at) == (conn.id, True, None)
        assert parent_access.authenticated(session, parent_session)[0].id == consent.id
        with pytest.raises(ValueError):
            child_authorization.director_authenticated(session, director_session)
        with pytest.raises(ValueError):
            child_authorization.consume_director_link(session, "family-director-link", pin="1357", name="", accept="yes")


def test_unbound_private_permission_requires_resolution_by_its_grantor(play_pg, adult_case, monkeypatch):
    c = adult_case
    monkeypatch.setattr(child_authorization, "notice_policy", lambda environment=None: ("synthetic", "", "a" * 64))
    with play_pg.begin() as session:
        student = profile(session, 8)
        _, _, permission = access_rows(session, student, "matched-only")
        permission.email = c.adult.email
    preserved = records(play_pg, TrustedVerifier, DirectorPermission, ParentAccess, ConsentEvidence, WoodchuckProfile)
    with pytest.raises(AdultDeletionHold, match="adult_unbound_director_review_required"):
        c.run()
    assert records(play_pg, *preserved) == preserved
    assert not list(c.register.glob("*.json"))


def test_prior_invitations_cannot_reopen_access_but_fresh_invitations_create_distinct_identity(play_pg, adult_case, monkeypatch):
    c = adult_case
    with play_pg.begin() as session:
        student = profile(session, 6)
        declare_age(session, student.id, "13to17")
        org = Organization(name="Unprovisioned School", organization_type="school", created_by_verifier_id=c.colleague.id)
        session.add(org)
        session.flush()
        earlier = c.now - timedelta(minutes=12)
        old_program = create_program_invitation(org.id, c.adult.email, now=earlier)
        with monkeypatch.context() as patch:
            patch.setattr(verifiers, "_utc_now", lambda: earlier)
            invite = create_trusted_verifier_invitation(session, profile=student, email=c.adult.email,
                                                       role="verifier", commit=False)
            invite.invitation.created_at = invite.invitation.updated_at = earlier
    assert c.run()["status"] == "adult_deletion_applied"
    with play_pg() as session, pytest.raises(classroom.ClassroomDenied):
        classroom.accept_program_invitation(session, token=old_program, pin="1357", display_name="Replacement")
    with play_pg() as session, pytest.raises(ValueError):
        accept_trusted_verifier_invitation(session, token=invite.token, display_name="Replacement", pin="1357")
    with play_pg.begin() as session:
        fresh = create_program_invitation(org.id, c.adult.email)
        program = classroom.accept_program_invitation(session, token=fresh, pin="9753", display_name="New account")
        assert program.owner_verifier_id != c.adult.id
    with play_pg.begin() as session:
        new_invite = reissue_trusted_verifier_invitation(session, profile=session.get(WoodchuckProfile, student.id),
                                                         invitation_id=invite.invitation.id, commit=False)
    with play_pg() as session:
        accepted = accept_trusted_verifier_invitation(session, token=new_invite.token, display_name="New account", pin="9753")
        assert accepted.verifier.id == program.owner_verifier_id
        assert accepted.connection.verifier_id != c.adult.id


def test_expired_program_token_stays_invalid_and_receipt_survives_marker_purge(play_pg, adult_case, monkeypatch):
    c = adult_case
    with play_pg.begin() as session:
        org = Organization(name="Synthetic School", organization_type="school", created_by_verifier_id=c.colleague.id)
        session.add(org)
        session.flush()
        token = create_program_invitation(org.id, c.adult.email, now=c.now - timedelta(seconds=1))
        assert classroom_invitation.parse_program_invitation(token, now=c.now).organization_id == org.id
    receipt = c.run()
    future = c.now + timedelta(days=7, seconds=1)
    with play_pg.begin() as session:
        assert purge_expired(session, now=c.now + timedelta(days=7, seconds=-1)).rowcount == 0
        assert purge_expired(session, now=future).rowcount == 1
    assert receipt == c.run(status_only=True, clock=lambda: future)

    class FutureTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future.astimezone(tz)

    monkeypatch.setattr(classroom_invitation, "datetime", FutureTime)
    with play_pg.begin() as session:
        with pytest.raises(classroom.ClassroomDenied):
            classroom.accept_program_invitation(session, token=token, pin="1357", display_name="Replacement")
        assert session.get(ClassroomProgram, org.id) is None


def test_unused_complimentary_membership_does_not_block_account_deletion(play_pg, adult_case):
    c = adult_case
    with play_pg.begin() as session:
        account = BillingAccount(verifier_id=c.adult.id)
        session.add(account)
        session.flush()
        session.add(Membership(billing_account_id=account.id, source="manual", status="active", starts_at=c.now))
    billing = records(play_pg, BillingAccount, Membership, MembershipSeat, CheckoutAttempt, ProviderSubscription)
    assert c.run()["status"] == "adult_deletion_applied"
    assert records(play_pg, *billing) == billing
