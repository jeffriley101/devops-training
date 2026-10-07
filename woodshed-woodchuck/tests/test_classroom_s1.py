"""S1 authority and negative capabilities using synthetic, disposable identities."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app import account_routes, classroom, main, session_revocations, verifier_routes
from app.age_models import AccountPrivacy
from app.band_director_dashboard import dashboard_metrics
from app.classroom_models import (
    ClassroomAuditEvent, ClassroomClass, ClassroomMembershipPeriod,
    ClassroomOwnershipTransfer, ClassroomProgram, ClassroomRoleGrant,
    ClassroomStudentMembership, ClassroomTeachingAssignment,
)
from app.db import Base
from app.models import (
    BillingAccount, Membership, MembershipSeat,
    Organization, PracticeChart, PracticeChartVerification, ProfileCapability, StudentOrganizationMembership,
    StudentVerifierConnection, TrustedVerifier, TrustedVerifierInvitation,
    VerifierOrganizationMembership, WoodchuckProfile,
)
from app.security import hash_pin, verify_pin
from app.verifiers import accepted_active_verifier_students, band_director_students


NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
DENIED = (ValueError, PermissionError)


@pytest.fixture(scope="module")
def adult_pin_hash():
    return hash_pin("2468")


@pytest.fixture
def classroom_db(monkeypatch, adult_pin_hash):
    monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
    monkeypatch.setenv("CLASSROOM_S1_LOCAL_PROVISIONING", "1")
    monkeypatch.setenv("CLASSROOM_S1_PROVISIONER_IDS", "1")
    with TemporaryDirectory(prefix="classroom-s1-tests-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'synthetic.db'}")

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add_all([
                TrustedVerifier(id=index, email=f"adult{index}@example.test",
                                display_name=f"Adult {index}", pin_hash=adult_pin_hash)
                for index in range(1, 5)
            ])
            session.flush()
            session.add_all([
                Organization(id=index, name=f"Program {index}", organization_type="music",
                             created_by_verifier_id=1)
                for index in (1, 2)
            ])
            session.add(WoodchuckProfile(
                id=1, woodchuck_id="WC-SYNTHETIC", display_name="Private Student",
                pin_hash=adult_pin_hash, instrument="Trumpet", level="Beginner",
                goal="Build daily consistency",
            ))
            session.flush()
            # Explicit synthetic declaration makes the existing free path eligible;
            # missing age evidence must continue to deny access in legacy tests.
            session.add(AccountPrivacy(profile_id=1, age_band="13to17",
                                       declared_at=NOW, public_from=NOW))
            session.commit()
        yield factory
        engine.dispose()


def actor(session, adult_id=1):
    return classroom.authenticate_adult(
        session, email=f"adult{adult_id}@example.test", pin="2468",
    )


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def provision(session, program_id=1, owner_id=1):
    return classroom.provision_program(
        session, actor=actor(session), organization_id=program_id,
        owner=actor(session, owner_id),
    )


def create_class(session, program_id=1, name="Concert Band", adult_id=1):
    return classroom.create_class(
        session, actor=actor(session, adult_id), program_id=program_id,
        display_name=name,
    )


def test_direct_synthetic_onboarding_creates_only_adult_credentials(classroom_db):
    with classroom_db() as session:
        adult_id = classroom.onboard_adult(
            session, actor=actor(session), email="  NEW@Example.Test  ",
            display_name="  New Adult  ", pin="1357",
        )
        session.commit()
        adult = session.get(TrustedVerifier, adult_id)
        assert adult.email == "new@example.test"
        assert adult.display_name == "New Adult"
        assert adult.pin_hash != "1357" and verify_pin("1357", adult.pin_hash)
        assert count(session, TrustedVerifier) == 5
        assert count(session, WoodchuckProfile) == 1
        for model in (StudentVerifierConnection, TrustedVerifierInvitation, ClassroomProgram,
                      ClassroomRoleGrant, ClassroomTeachingAssignment, ClassroomStudentMembership,
                      ProfileCapability, BillingAccount, Membership, MembershipSeat):
            assert count(session, model) == 0


@pytest.mark.parametrize("pin", ["0000", "2468"])
def test_existing_email_is_never_claimed_or_credentials_changed(classroom_db, pin):
    with classroom_db() as session:
        before = session.get(TrustedVerifier, 2)
        original = (before.display_name, before.pin_hash, before.updated_at)
        with pytest.raises(DENIED):
            classroom.onboard_adult(
                session, actor=actor(session), email="  ADULT2@EXAMPLE.TEST ",
                display_name="Impersonator", pin=pin,
            )
        assert (before.display_name, before.pin_hash, before.updated_at) == original
        assert count(session, TrustedVerifier) == 4
        assert count(session, ClassroomProgram) == 0
        assert count(session, StudentVerifierConnection) == 0


@pytest.mark.parametrize("email,pin", [("invalid", "2468"), ("ok@example.test", "123"),
                                       ("ok@example.test", "abcd")])
def test_onboarding_reuses_credential_validation(classroom_db, email, pin):
    with classroom_db() as session:
        with pytest.raises(DENIED):
            classroom.onboard_adult(session, actor=actor(session), email=email,
                                   display_name="New", pin=pin)
        assert count(session, TrustedVerifier) == 4


def test_missing_identity_proof_keeps_runtime_onboarding_unavailable(classroom_db, monkeypatch):
    monkeypatch.delenv("CLASSROOM_S1_LOCAL_PROVISIONING")
    with classroom_db() as session:
        with pytest.raises(classroom.IdentityProofUnavailable):
            classroom.onboard_adult(session, actor=actor(session), email="new@example.test",
                                   display_name="New", pin="1357")
        assert count(session, TrustedVerifier) == 4


def test_local_onboarding_refuses_nonreserved_target_addresses(classroom_db):
    with classroom_db() as session:
        with pytest.raises(classroom.IdentityProofUnavailable):
            classroom.onboard_adult(session, actor=actor(session), email="adult@example.com",
                                   display_name="Outside Synthetic Scope", pin="1357")
        assert count(session, TrustedVerifier) == 4


def test_existing_ordinary_adult_authenticates_but_cannot_use_synthetic_provisioning(classroom_db):
    with classroom_db() as session:
        session.get(TrustedVerifier, 1).email = "existing-adult@example.com"
        session.commit()
        authenticated = classroom.authenticate_adult(
            session, email=" EXISTING-ADULT@EXAMPLE.COM ", pin="2468",
        )
        assert classroom.my_classes(session, actor=authenticated)["programs"] == []
        with pytest.raises(classroom.IdentityProofUnavailable):
            classroom.provision_program(session, actor=authenticated, organization_id=1,
                                        owner=authenticated)
        assert count(session, ClassroomProgram) == 0


def test_local_provisioning_requires_server_authorized_authenticated_actor(classroom_db):
    with classroom_db() as session:
        with pytest.raises(DENIED):
            classroom.provision_program(session, actor=actor(session, 2),
                                        organization_id=1, owner=actor(session, 2))
        assert count(session, ClassroomProgram) == 0
        assert count(session, ClassroomAuditEvent) == 0


@pytest.mark.parametrize("email,pin", [("adult2@example.test", "0000"),
                                       ("unknown@example.test", "2468")])
def test_existing_adults_must_supply_their_own_credentials(classroom_db, email, pin):
    with classroom_db() as session:
        with pytest.raises(DENIED):
            classroom.authenticate_adult(session, email=email, pin=pin)
        assert count(session, ClassroomProgram) == 0


@pytest.mark.parametrize("forged", [1, True, {"verifier_id": 1, "role": "owner"}, None])
def test_actor_ids_and_role_claims_are_not_authentication(classroom_db, forged):
    with classroom_db() as session:
        with pytest.raises(DENIED):
            classroom.provision_program(session, actor=forged, organization_id=1,
                                        owner=actor(session))
        assert count(session, ClassroomProgram) == 0


def test_authenticated_actor_cannot_be_reused_in_another_session(classroom_db):
    with classroom_db() as first, classroom_db() as second:
        authenticated = actor(first)
        with pytest.raises(DENIED):
            classroom.provision_program(second, actor=authenticated, organization_id=1,
                                        owner=actor(second))


def test_authenticated_actor_expires_at_transaction_boundary(classroom_db):
    with classroom_db() as session:
        authenticated = actor(session)
        session.commit()
        with pytest.raises(DENIED):
            classroom.provision_program(session, actor=authenticated, organization_id=1,
                                        owner=actor(session))


def test_credential_change_invalidates_loaded_actor(classroom_db):
    with classroom_db() as session:
        authenticated = actor(session)
        session.get(TrustedVerifier, 1).pin_hash = hash_pin("1357")
        session.flush()
        with pytest.raises(DENIED):
            classroom.provision_program(session, actor=authenticated, organization_id=1,
                                        owner=authenticated)
        assert count(session, ClassroomProgram) == 0


def test_legacy_creation_and_relationships_never_provision_program(classroom_db):
    with classroom_db() as session:
        session.add_all([
            StudentOrganizationMembership(organization_id=1, profile_id=1),
            VerifierOrganizationMembership(organization_id=1, verifier_id=1),
            StudentVerifierConnection(profile_id=1, verifier_id=1,
                                      role="band_director", status="accepted"),
            ProfileCapability(profile_id=1, capability="band_director"),
        ])
        session.commit()
        assert count(session, ClassroomProgram) == 0
        assert count(session, ClassroomRoleGrant) == 0
        assert count(session, ClassroomTeachingAssignment) == 0
        with pytest.raises(DENIED):
            create_class(session)


def test_explicit_owner_and_head_are_independent_and_cannot_be_removed(classroom_db):
    with classroom_db() as session:
        program = provision(session, owner_id=2)
        assert program.owner_verifier_id == 2
        head = session.scalar(select(ClassroomRoleGrant))
        assert head.verifier_id == 2 and head.role == "head_director"
        assert count(session, ClassroomTeachingAssignment) == 0
        with pytest.raises(DENIED):
            classroom.grant_role(session, actor=actor(session, 2), program_id=1,
                                 verifier_id=3, role="owner")
        classroom.revoke_role(session, actor=actor(session, 2), program_id=1,
                              grant_id=head.id)
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 2
        assert head.ended_reason == "revoked" and head.ended_by_verifier_id == 2
        assert head.ended_at is not None
        with pytest.raises(IntegrityError), session.begin_nested():
            program.owner_verifier_id = None
            session.flush()


def test_multiple_roles_programs_and_revocation_are_independently_scoped(classroom_db):
    with classroom_db() as session:
        provision(session)
        admin = classroom.grant_role(session, actor=actor(session), program_id=1,
                                    verifier_id=2, role="admin")
        billing = classroom.grant_role(session, actor=actor(session), program_id=1,
                                      verifier_id=2, role="billing")
        session.commit()
        provision(session, program_id=2, owner_id=3)
        classroom.grant_role(session, actor=actor(session, 3), program_id=2,
                             verifier_id=2, role="code_manager")
        session.commit()
        create_class(session, adult_id=2)
        session.commit()
        with pytest.raises(DENIED):
            create_class(session, program_id=2, adult_id=2)
        session.rollback()
        classroom.revoke_role(session, actor=actor(session), program_id=1, grant_id=admin.id)
        with pytest.raises(DENIED):
            create_class(session, adult_id=2)
        assert session.get(ClassroomRoleGrant, billing.id).ended_at is None
        assert count(session, ClassroomProgram) == 2
        assert count(session, ClassroomClass) == 1


@pytest.mark.parametrize("role", ["head_director", "admin", "code_manager", "billing"])
def test_delegated_roles_cannot_self_promote_or_mark_entitlement(classroom_db, role):
    with classroom_db() as session:
        provision(session)
        classroom.grant_role(session, actor=actor(session), program_id=1,
                             verifier_id=2, role=role)
        with pytest.raises(DENIED):
            classroom.propose_transfer(session, actor=actor(session, 2), program_id=1,
                                       recipient_id=2)
        with pytest.raises(DENIED):
            classroom.grant_role(session, actor=actor(session, 2), program_id=1,
                                 verifier_id=2, role="owner")
        metadata = classroom.my_classes(session, actor=actor(session, 2))
        assert metadata["paid_capabilities"] == "not_implemented"
        assert metadata["student_reporting"] == "not_implemented"


def test_assignments_support_codirectors_departure_and_reassignment(classroom_db):
    with classroom_db() as session:
        provision(session)
        first, second = create_class(session), create_class(session, name="Jazz")
        assignments = []
        for room, adult_id in ((first, 2), (first, 3), (second, 2)):
            assignments.append(classroom.assign_teacher(
                session, actor=actor(session), program_id=1, class_id=room.id,
                verifier_id=adult_id,
            ))
        original = assignments[0]
        classroom.end_teaching(session, actor=actor(session, 2), program_id=1,
                               assignment_id=original.id, reason="departed")
        replacement = classroom.assign_teacher(
            session, actor=actor(session), program_id=1, class_id=first.id, verifier_id=2,
        )
        assert replacement.id != original.id
        assert original.ended_at is not None and original.ended_reason == "departed"
        assert count(session, ClassroomClass) == 2
        assert count(session, ClassroomTeachingAssignment) == 4
        assert count(session, ClassroomStudentMembership) == 0
        assert count(session, StudentVerifierConnection) == 0
        assert accepted_active_verifier_students(session, verifier_id=2) == []
        assert band_director_students(session, verifier_id=2) == []
        with pytest.raises(DENIED):
            classroom.end_teaching(session, actor=actor(session, 3), program_id=1,
                                   assignment_id=replacement.id, reason="departed")


def test_cross_program_class_and_grant_substitution_are_denied(classroom_db):
    with classroom_db() as session:
        provision(session)
        first = create_class(session)
        role = classroom.grant_role(session, actor=actor(session), program_id=1,
                                   verifier_id=2, role="billing")
        session.commit()
        provision(session, program_id=2)
        session.commit()
        with pytest.raises(DENIED):
            classroom.assign_teacher(session, actor=actor(session), program_id=2,
                                      class_id=first.id, verifier_id=2)
        with pytest.raises(DENIED):
            classroom.revoke_role(session, actor=actor(session), program_id=2,
                                  grant_id=role.id)
        with pytest.raises(DENIED):
            classroom.my_classes(session, actor=actor(session), program_id=2,
                                 class_id=first.id)
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(ClassroomTeachingAssignment(program_id=2, class_id=first.id,
                        verifier_id=2, granted_by_verifier_id=1))
            session.flush()


def test_transfer_requires_exact_authenticated_recipient_and_cannot_replay(classroom_db):
    with classroom_db() as session:
        provision(session)
        room = create_class(session)
        session.add(ClassroomStudentMembership(class_id=room.id, profile_id=1))
        session.flush()
        proposal = classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                               recipient_id=2)
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 1
        with pytest.raises(DENIED):
            classroom.accept_transfer(session, actor=actor(session, 3), program_id=1,
                                       transfer_id=proposal.id)
        with pytest.raises(TypeError):
            classroom.accept_transfer(session, actor=actor(session), program_id=1,
                                       transfer_id=proposal.id, recipient_accepted=True)
        classroom.accept_transfer(session, actor=actor(session, 2), program_id=1,
                                  transfer_id=proposal.id)
        with pytest.raises(DENIED):
            classroom.accept_transfer(session, actor=actor(session, 2), program_id=1,
                                       transfer_id=proposal.id)
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 2
        assert count(session, ClassroomClass) == 1
        assert count(session, ClassroomStudentMembership) == 1
        assert count(session, ClassroomTeachingAssignment) == 0
        transferred = session.scalars(select(ClassroomAuditEvent).where(
            ClassroomAuditEvent.action == "ownership_transferred")).all()
        assert len(transferred) == 1
        assert (transferred[0].actor_verifier_id, transferred[0].old_owner_id,
                transferred[0].new_owner_id, transferred[0].transfer_id) == (2, 1, 2, proposal.id)
        assert transferred[0].occurred_at is not None
        with pytest.raises(DENIED):
            create_class(session)
        assert session.scalar(select(ClassroomRoleGrant).where(
            ClassroomRoleGrant.verifier_id == 1)).role == "head_director"


@pytest.mark.parametrize("resolution", ["cancelled", "superseded"])
def test_cancelled_or_superseded_transfer_cannot_be_accepted(classroom_db, resolution):
    with classroom_db() as session:
        provision(session)
        proposal = classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                               recipient_id=2)
        if resolution == "cancelled":
            classroom.cancel_transfer(session, actor=actor(session), program_id=1,
                                      transfer_id=proposal.id)
        else:
            classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                       recipient_id=3)
        with pytest.raises(DENIED):
            classroom.accept_transfer(session, actor=actor(session, 2), program_id=1,
                                       transfer_id=proposal.id)
        assert proposal.status == resolution
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 1
        assert session.scalar(select(func.count()).select_from(ClassroomAuditEvent).where(
            ClassroomAuditEvent.action == "ownership_transferred")) == 0


def test_transfer_preserves_only_separately_granted_outgoing_authority(classroom_db):
    with classroom_db() as session:
        provision(session)
        admin = classroom.grant_role(session, actor=actor(session), program_id=1,
                                    verifier_id=1, role="admin")
        proposal = classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                               recipient_id=2)
        classroom.accept_transfer(session, actor=actor(session, 2), program_id=1,
                                  transfer_id=proposal.id)
        assert create_class(session).program_id == 1
        assert admin.ended_at is None
        with pytest.raises(DENIED):
            classroom.grant_role(session, actor=actor(session), program_id=1,
                                 verifier_id=3, role="billing")
        assert count(session, ClassroomTeachingAssignment) == 0


def test_transfer_id_cannot_be_substituted_across_programs(classroom_db):
    with classroom_db() as session:
        provision(session)
        proposal = classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                               recipient_id=2)
        proposal_id = proposal.id
        session.commit()
        provision(session, program_id=2, owner_id=3)
        session.commit()
        with pytest.raises(DENIED):
            classroom.accept_transfer(session, actor=actor(session, 2), program_id=2,
                                       transfer_id=proposal_id)
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 1
        assert session.get(ClassroomProgram, 2).owner_verifier_id == 3


def test_caller_rollback_preserves_original_owner_and_no_success_audit(classroom_db):
    with classroom_db() as session:
        provision(session)
        proposal = classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                               recipient_id=2)
        proposal_id = proposal.id
        session.commit()
        classroom.accept_transfer(session, actor=actor(session, 2), program_id=1,
                                  transfer_id=proposal_id)
        session.rollback()
    with classroom_db() as session:
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 1
        assert session.get(ClassroomOwnershipTransfer, proposal_id).status == "pending"
        assert session.scalar(select(func.count()).select_from(ClassroomAuditEvent).where(
            ClassroomAuditEvent.action == "ownership_transferred")) == 0


def test_audit_failure_cannot_leave_committable_partial_transfer(classroom_db, monkeypatch):
    with classroom_db() as session:
        provision(session)
        proposal = classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                               recipient_id=2)
        session.commit()
        original_audit = classroom._audit

        def fail_audit(*args, **kwargs):
            if args[3] == "ownership_transferred":
                raise RuntimeError("synthetic audit failure")
            return original_audit(*args, **kwargs)

        monkeypatch.setattr(classroom, "_audit", fail_audit)
        with pytest.raises(RuntimeError, match="synthetic audit failure"):
            classroom.accept_transfer(session, actor=actor(session, 2), program_id=1,
                                       transfer_id=proposal.id)
        session.commit()
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 1
        assert session.get(ClassroomOwnershipTransfer, proposal.id).status == "pending"
        assert session.scalar(select(func.count()).select_from(ClassroomAuditEvent).where(
            ClassroomAuditEvent.action == "ownership_transferred")) == 0


@pytest.mark.parametrize("authority", ["owner", "admin", "billing", "teacher"])
def test_real_section_resolver_never_exposes_new_student_data(classroom_db, authority):
    with classroom_db() as session:
        provision(session)
        room = create_class(session)
        subject = 1 if authority == "owner" else 2
        if authority == "teacher":
            classroom.assign_teacher(session, actor=actor(session), program_id=1,
                                      class_id=room.id, verifier_id=subject)
        elif authority != "owner":
            classroom.grant_role(session, actor=actor(session), program_id=1,
                                 verifier_id=subject, role=authority)
        membership = ClassroomStudentMembership(class_id=room.id, profile_id=1)
        session.add(membership)
        session.flush()
        session.add(ClassroomMembershipPeriod(membership_id=membership.id, starts_at=NOW,
                                             state="active"))
        session.flush()
        authenticated = actor(session, subject)
        sections = classroom.director_sections(session, actor=authenticated, today=NOW.date())
        assert sections["connected_students"] == dashboard_metrics(
            session, verifier_id=subject, today=NOW.date(),
        )
        assert sections["connected_students"]["students"] == []
        metadata = sections["my_classes"]
        assert set(metadata) == {"status", "programs", "paid_capabilities", "student_reporting"}
        assert metadata["paid_capabilities"] == metadata["student_reporting"] == "not_implemented"
        assert metadata["status"] == "foundation_only"
        assert "Private Student" not in repr(metadata)
        for program in metadata["programs"]:
            assert set(program) == {"program_id", "display_name", "owner", "roles", "classes"}
            for resolved in program["classes"]:
                assert set(resolved) == {"id", "display_name", "teaching"}
        with pytest.raises(DENIED):
            classroom.require_student_reporting(session, actor=authenticated, program_id=1,
                                                class_id=room.id)


def test_unassigned_class_metadata_hidden_from_teacher_and_billing(classroom_db):
    with classroom_db() as session:
        provision(session)
        assigned = create_class(session)
        other = create_class(session, name="Other Class")
        classroom.assign_teacher(session, actor=actor(session), program_id=1,
                                  class_id=assigned.id, verifier_id=2)
        classroom.grant_role(session, actor=actor(session), program_id=1,
                             verifier_id=3, role="billing")
        teacher = classroom.my_classes(session, actor=actor(session, 2))
        assert teacher["programs"][0]["classes"] == [
            {"id": assigned.id, "display_name": "Concert Band", "teaching": True}]
        assert classroom.my_classes(session, actor=actor(session, 3))["programs"][0]["classes"] == []
        with pytest.raises(DENIED):
            classroom.my_classes(session, actor=actor(session, 2), program_id=1, class_id=other.id)


def test_section_resolution_refreshes_cached_owner_after_transfer(classroom_db):
    with classroom_db() as session:
        provision(session)
        create_class(session)
        transfer = classroom.propose_transfer(session, actor=actor(session), program_id=1,
                                              recipient_id=2)
        transfer_id = transfer.id
        session.commit()
    with classroom_db() as old_owner_session:
        authenticated = actor(old_owner_session)
        cached = old_owner_session.get(ClassroomProgram, 1)
        assert cached.owner_verifier_id == 1
        with classroom_db() as recipient_session:
            classroom.accept_transfer(recipient_session, actor=actor(recipient_session, 2),
                                      program_id=1, transfer_id=transfer_id)
            recipient_session.commit()
        section = classroom.my_classes(old_owner_session, actor=authenticated)["programs"][0]
        assert section["owner"] is False
        assert section["roles"] == ["head_director"]
        assert section["classes"] == []


def test_child_link_and_student_capability_are_not_general_adult_authentication(classroom_db):
    with classroom_db() as session:
        provision(session)
        room = create_class(session)
        session.add(ProfileCapability(profile_id=1, capability="band_director"))
        session.flush()
        for forged in (session.get(WoodchuckProfile, 1),
                       {"child_director_permission": 1, "child_director_secret": "synthetic"},
                       {"child_parent_consent": 1, "child_parent_secret": "synthetic"}):
            with pytest.raises(DENIED):
                classroom.my_classes(session, actor=forged, program_id=1, class_id=room.id)
        assert count(session, ClassroomRoleGrant) == 1
        assert count(session, ClassroomTeachingAssignment) == 0


def test_default_off_preserves_free_dashboard_without_classroom_queries(classroom_db, monkeypatch):
    monkeypatch.delenv("CLASSROOM_S1_ENABLED")
    with classroom_db() as session:
        session.add(StudentVerifierConnection(profile_id=1, verifier_id=1,
                                              role="band_director", status="accepted"))
        session.commit()
        expected = dashboard_metrics(session, verifier_id=1, today=NOW.date())
        assert expected["students"][0]["display_name"] == "Private Student"
        authenticated = actor(session)
        statements = []

        def observe(connection, cursor, statement, parameters, context, many):
            statements.append(statement)

        event.listen(session.get_bind(), "before_cursor_execute", observe)
        try:
            actual = classroom.director_sections(session, actor=authenticated, today=NOW.date())
            with pytest.raises(DENIED):
                classroom.create_class(session, actor=authenticated, program_id=1, display_name="No")
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", observe)
        assert actual["connected_students"] == expected
        assert actual["my_classes"]["status"] == "disabled"
        assert not any("classroom_" in statement.lower() for statement in statements)


def test_missing_schema_is_compatible_when_disabled_and_errors_when_enabled(classroom_db, monkeypatch):
    engine = classroom_db.kw["bind"]
    Base.metadata.drop_all(engine, tables=[table for name, table in Base.metadata.tables.items()
                                          if name.startswith("classroom_")])
    with classroom_db() as session:
        monkeypatch.delenv("CLASSROOM_S1_ENABLED")
        authenticated = actor(session)
        result = classroom.director_sections(session, actor=authenticated, today=NOW.date())
        assert result["my_classes"]["status"] == "disabled"
        monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
        with pytest.raises(OperationalError):
            classroom.my_classes(session, actor=authenticated)


def test_sqlite_without_foreign_key_enforcement_cannot_mutate_authority(classroom_db):
    engine = classroom_db.kw["bind"]
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
    with classroom_db() as session:
        with pytest.raises(DENIED):
            provision(session)
        assert count(session, ClassroomProgram) == 0


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("role", ["verifier", "band_director"])
def test_existing_free_invitation_login_review_and_revocation(classroom_db, monkeypatch, enabled, role):
    """Real existing route authorization, with explicit valid synthetic age evidence."""
    from app.email_service import DeliveryResult, EmailService

    monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1" if enabled else "0")
    for module in (main, account_routes, verifier_routes, session_revocations):
        monkeypatch.setattr(module, "SessionLocal", classroom_db)
    deliveries = []

    def synthetic_delivery(self, **values):
        deliveries.append(values)
        return DeliveryResult(True, "sent")

    monkeypatch.setattr(EmailService, "send_invitation", synthetic_delivery)
    with TestClient(main.app) as student, TestClient(main.app) as adult, TestClient(main.app) as other:
        assert student.post("/account/login", data={
            "woodchuck_id": "WC-SYNTHETIC", "pin": "2468",
        }).status_code == 200
        invited = student.post("/trusted-verifiers/invitations", data={
            "email": "new-free-adult@example.test", "role": role,
        })
        assert invited.status_code == 200 and len(deliveries) == 1
        token = invited.json()["invitation_token"]
        accepted = adult.post(f"/trusted-verifiers/invitations/{token}/accept", data={
            "display_name": "Free Adult", "pin": "1357",
        })
        assert accepted.status_code == 200
        connection_id = accepted.json()["connection"]["id"]
        verifier_id = accepted.json()["verifier"]["id"]
        assert adult.post("/trusted-verifiers/logout").status_code == 200
        assert adult.post("/trusted-verifiers/login", data={
            "email": "new-free-adult@example.test", "pin": "1357",
        }).status_code == 200
        page = adult.get("/trusted-verifiers/dashboard")
        assert (page.context["student"] is not None) == (role == "verifier")
        director = adult.get("/band-director/dashboard")
        assert ("Private Student" in director.text) == (role == "band_director")
        assert director.headers["cache-control"] == "no-store"
        with classroom_db() as session:
            assert count(session, ClassroomProgram) == 0
            assert count(session, ClassroomRoleGrant) == 0
            assert count(session, ClassroomTeachingAssignment) == 0
            authenticated = classroom.authenticate_adult(
                session, email="new-free-adult@example.test", pin="1357",
            )
            section = classroom.my_classes(session, actor=authenticated)
            assert section.get("programs", []) == []
            if role == "verifier":
                for minutes in (10, 20):
                    chart = PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=minutes,
                                          instrument="Trumpet", created_at=NOW)
                    session.add(chart)
                    session.flush()
                    session.add(PracticeChartVerification(practice_chart_id=chart.id,
                                verifier_id=verifier_id, status="pending"))
                session.commit()
        if role == "verifier":
            pending = adult.get("/trusted-verifiers/practice-charts").json()["pending_charts"]
            assert len(pending) == 2
            endpoint = f'/trusted-verifiers/practice-charts/{pending[0]["verification_id"]}/respond'
            assert other.post("/trusted-verifiers/login", data={
                "email": "adult2@example.test", "pin": "2468",
            }).status_code == 200
            assert other.post(endpoint, json={"decision": "approved"}).status_code == 404
            assert adult.post(endpoint, json={"decision": "approved"}).status_code == 200
            assert adult.post(endpoint, json={"decision": "approved"}).status_code == 400
        assert student.delete(f"/trusted-verifiers/connections/{connection_id}").status_code == 200
        assert adult.get("/trusted-verifiers/dashboard").context["student"] is None
        assert "Private Student" not in adult.get("/band-director/dashboard").text
        if role == "verifier":
            endpoint = f'/trusted-verifiers/practice-charts/{pending[1]["verification_id"]}/respond'
            revoked = adult.post(endpoint, json={"decision": "approved"})
            assert revoked.status_code == 400
            assert "no longer connected" in revoked.json()["detail"]
            with classroom_db() as session:
                assert session.get(PracticeChartVerification,
                                   pending[1]["verification_id"]).status == "pending"


def test_membership_anchor_multiple_classes_and_preserved_periods(classroom_db):
    with classroom_db() as session:
        provision(session)
        first, second = create_class(session), create_class(session, name="Jazz")
        anchors = [ClassroomStudentMembership(class_id=room.id, profile_id=1)
                   for room in (first, second)]
        session.add_all(anchors)
        session.flush()
        session.add_all([
            ClassroomMembershipPeriod(membership_id=anchors[0].id, starts_at=NOW,
                                      ended_at=NOW + timedelta(days=1), state="active",
                                      ended_reason="changed"),
            ClassroomMembershipPeriod(membership_id=anchors[0].id,
                                      starts_at=NOW + timedelta(days=1), state="held"),
            ClassroomMembershipPeriod(membership_id=anchors[1].id, starts_at=NOW,
                                      state="active"),
        ])
        session.commit()
        assert count(session, ClassroomStudentMembership) == 2
        assert count(session, ClassroomMembershipPeriod) == 3
        assert count(session, StudentVerifierConnection) == 0
        assert session.get(WoodchuckProfile, 1).woodchuck_id == "WC-SYNTHETIC"
        assert session.get(WoodchuckProfile, 1).status == "active"


@pytest.mark.parametrize("violation", ["duplicate_anchor", "missing_student", "missing_class",
                                        "two_open_periods", "backwards_period", "bad_state",
                                        "missing_end_reason"])
def test_membership_database_constraints(classroom_db, violation):
    with classroom_db() as session:
        provision(session)
        room = create_class(session)
        membership = ClassroomStudentMembership(class_id=room.id, profile_id=1)
        session.add(membership)
        session.flush()
        session.add(ClassroomMembershipPeriod(membership_id=membership.id, starts_at=NOW,
                                             state="active"))
        session.commit()
        if violation == "duplicate_anchor":
            row = ClassroomStudentMembership(class_id=room.id, profile_id=1)
        elif violation == "missing_student":
            row = ClassroomStudentMembership(class_id=room.id, profile_id=999)
        elif violation == "missing_class":
            row = ClassroomStudentMembership(class_id=999, profile_id=1)
        else:
            values = dict(membership_id=membership.id, starts_at=NOW + timedelta(days=1),
                          state="active")
            if violation == "backwards_period":
                values.update(ended_at=NOW, ended_reason="departed")
            elif violation == "bad_state":
                values.update(state="paid", ended_at=NOW + timedelta(days=2),
                              ended_reason="departed")
            elif violation == "missing_end_reason":
                values.update(ended_at=NOW + timedelta(days=2))
            row = ClassroomMembershipPeriod(**values)
        session.add(row)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        assert count(session, ClassroomStudentMembership) == 1
        assert count(session, ClassroomMembershipPeriod) == 1


@pytest.mark.parametrize("relationship", ["role", "teaching"])
@pytest.mark.parametrize("missing", ["ended_reason", "ended_by_verifier_id"])
def test_closed_authority_requires_complete_revocation_evidence(classroom_db, relationship, missing):
    with classroom_db() as session:
        provision(session)
        room = create_class(session)
        values = dict(program_id=1, verifier_id=2, granted_by_verifier_id=1,
                      starts_at=NOW, ended_at=NOW + timedelta(days=1),
                      ended_reason="revoked", ended_by_verifier_id=1)
        values.pop(missing)
        row = (ClassroomRoleGrant(role="admin", **values) if relationship == "role"
               else ClassroomTeachingAssignment(class_id=room.id, **values))
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(row)
            session.flush()
