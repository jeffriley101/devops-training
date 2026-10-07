"""Synthetic S2 contract tests; Classroom authority never becomes consumer access."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import classroom, classroom_s2 as service, child_authorization, site_admin
from app.age_models import AccountPrivacy
from app.age_privacy import eligible, utc
from app.child_models import ConsentEvidence
from app.classroom_models import (
    ClassroomS2AuditEvent, ClassroomClass, ClassroomEntitlement, ClassroomEntryCode,
    ClassroomMembershipHold, ClassroomMembershipPeriod,
    ClassroomProgram, ClassroomStudentMembership, ClassroomTeachingAssignment,
)
from app.db import Base
from app.memberships import student_has_full_access
from app.models import (
    BillingAccount, Membership, MembershipSeat, ProfileCapability,
    StudentOrganizationMembership, StudentVerifierConnection, TeamMembership,
    TesterEnrollment as Enrollment, WoodchuckProfile,
)
from test_classroom_s1 import actor, adult_pin_hash, classroom_db, count, provision


# S1 assignment timestamps use their existing real server clock. Keep the S2
# test clock ahead of those timestamps while independently testing boundaries.
NOW = datetime.now(timezone.utc) + timedelta(hours=1)


@pytest.fixture
def s2_db(classroom_db, monkeypatch):
    monkeypatch.setenv("CLASSROOM_S2_ENABLED", "1")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-classroom-s2-only-secret")
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "synthetic-classroom-s2-admin")
    monkeypatch.setattr(service, "_now", lambda: NOW)
    with classroom_db() as session:
        provision(session)
        provision(session, 2)
        session.commit()
    return classroom_db


def student(session):
    return service.authenticate_student(session, woodchuck_id="WC-SYNTHETIC", pin="2468")


def admin():
    request = SimpleNamespace(session={})
    site_admin.sign_in_site_admin(request, "synthetic-classroom-s2-admin")
    return service.site_admin_authentication(request)


def trial(session, program_id=1, adult_id=1):
    return service.start_trial(session, actor=actor(session, adult_id), program_id=program_id)


def create(session, program_id=1, name="Concert Band", adult_id=1):
    return service.create_class(session, actor=actor(session, adult_id),
                                program_id=program_id, display_name=name)


def code(session, class_id, preferred="BAND2026", program_id=1, adult_id=1):
    return service.issue_code(session, actor=actor(session, adult_id), program_id=program_id,
                              class_id=class_id, preferred=preferred)


def ready(session, *, program_id=1, preferred="BAND2026"):
    trial(session, program_id)
    row = create(session, program_id)
    entry, visible = code(session, row.id, preferred, program_id)
    return row, entry, visible


def join(session, visible="BAND2026", program_id=1, intent=None):
    return service.join_class(session, student=student(session), program_id=program_id,
                              code=None if intent else visible, intent=intent)


def institutional(session, *, class_limit=10, teacher_limit=2, status="active",
                  starts_at=NOW, ends_at=NOW + timedelta(days=365), program_id=1):
    return service.configure_institutional(session, admin=admin(), program_id=program_id,
        starts_at=starts_at, ends_at=ends_at, class_limit=class_limit,
        teacher_limit=teacher_limit, provenance="synthetic-review-001", status=status)


OFFSET_WINDOWS = [
    (-1, 1, -5, True), (-1, 1, 5, True),
    (1, 10, -5, False), (-10, -1, 5, False),
    (0, 1, 5, True), (-1, 0, -5, False),
]


def assert_institutional_offset_window(factory, start_hours, end_hours, offset, allowed):
    zone = timezone(timedelta(hours=offset))
    start = (NOW + timedelta(hours=start_hours)).astimezone(zone)
    end = (NOW + timedelta(hours=end_hours)).astimezone(zone)
    with factory() as session:
        institutional(session, starts_at=start, ends_at=end)
        session.commit()
    # A fresh identity map is essential: SQLite strips offsets when writing.
    with factory() as session:
        row = session.scalar(select(ClassroomEntitlement))
        assert utc(row.starts_at) == start.astimezone(timezone.utc)
        assert utc(row.ends_at) == end.astimezone(timezone.utc)
        assert service.entitlement_decision(session, 1).allowed is allowed


@pytest.mark.parametrize("start_hours,end_hours,offset,allowed", OFFSET_WINDOWS)
def test_institutional_offsets_preserve_approved_window(s2_db, start_hours, end_hours, offset, allowed):
    assert_institutional_offset_window(s2_db, start_hours, end_hours, offset, allowed)


def periods(session, membership_id):
    return list(session.scalars(select(ClassroomMembershipPeriod).where(
        ClassroomMembershipPeriod.membership_id == membership_id).order_by(
            ClassroomMembershipPeriod.starts_at)))


def test_trial_deliberate_program_scope_and_exact_fourteen_day_boundary(s2_db, monkeypatch):
    from app.stripe_billing import StripeProvider

    def unexpected_payment_provider(*args, **kwargs):
        pytest.fail("A Program trial must never initialize a payment provider")

    monkeypatch.setattr(StripeProvider, "__init__", unexpected_payment_provider)
    with s2_db() as session:
        assert not service.entitlement_decision(session, 1).allowed
        with pytest.raises(classroom.ClassroomDenied):
            trial(session, adult_id=2)
        row = trial(session)
        assert utc(row.ends_at) - utc(row.starts_at) == timedelta(days=14)
        decision = service.entitlement_decision(session, 1, at=NOW)
        assert decision.allowed and decision.source == "trial"
        assert decision.entitlement_id == row.id
        assert (decision.class_limit, decision.teacher_limit) == (10, 2)
        assert service.entitlement_decision(session, 1, at=NOW + timedelta(days=14, microseconds=-1)).allowed
        expired = service.entitlement_decision(session, 1, at=NOW + timedelta(days=14))
        assert not expired.allowed and expired.reason
        assert not service.entitlement_decision(session, 2).allowed
        with pytest.raises(classroom.ClassroomDenied):
            trial(session)
        assert count(session, Membership) == count(session, BillingAccount) == 0


def test_head_director_can_start_trial_billing_and_admin_cannot(s2_db):
    with s2_db() as session:
        for adult_id, role in ((2, "billing"), (3, "admin"), (4, "head_director")):
            classroom.grant_role(session, actor=actor(session), program_id=1,
                                 verifier_id=adult_id, role=role)
        for adult_id in (2, 3):
            with pytest.raises(classroom.ClassroomDenied):
                trial(session, adult_id=adult_id)
        assert trial(session, adult_id=4).id


def test_trial_cannot_restart_after_rotation_archive_ownership_or_expiry(s2_db, monkeypatch):
    with s2_db() as session:
        row, _, _ = ready(session)
        code(session, row.id, "ROTATED9")
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=row.id, state="archived")
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=row.id, state="active")
        transfer = classroom.propose_transfer(session, actor=actor(session), program_id=1, recipient_id=2)
        classroom.accept_transfer(session, actor=actor(session, 2), program_id=1, transfer_id=transfer.id)
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(days=15))
        with pytest.raises(classroom.ClassroomDenied):
            trial(session, adult_id=2)


def test_manual_entitlement_internal_only_and_continues_after_trial(s2_db, monkeypatch):
    with s2_db() as session:
        trial(session)
        with pytest.raises((classroom.ClassroomDenied, PermissionError)):
            service.configure_institutional(session, admin=actor(session), program_id=1,
                starts_at=NOW, ends_at=NOW + timedelta(days=365), class_limit=4,
                teacher_limit=3, provenance="synthetic-review-001")
        approved = institutional(session, class_limit=4, teacher_limit=3)
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(days=15))
        decision = service.entitlement_decision(session, 1)
        assert decision.allowed and decision.source == "institutional"
        assert decision.entitlement_id == approved.id
        assert (decision.class_limit, decision.teacher_limit) == (4, 3)
        assert not service.entitlement_decision(session, 2).allowed


def test_allowances_rotation_closure_archive_reactivation(s2_db):
    with s2_db() as session:
        institutional(session, class_limit=2)
        first, second = create(session, name="One"), create(session, name="Two")
        code(session, first.id)
        code(session, first.id, "ROTATED9")
        service.set_enrollment(session, actor=actor(session), program_id=1, class_id=first.id, open=False)
        with pytest.raises(classroom.ClassroomDenied):
            create(session, name="Over capacity")
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=first.id, state="archived")
        third = create(session, name="Three")
        with pytest.raises(classroom.ClassroomDenied):
            service.set_class_state(session, actor=actor(session), program_id=1, class_id=first.id, state="active")
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=third.id, state="archived")
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=first.id, state="active")
        assert session.get(ClassroomClass, first.id).id == first.id
        assert count(session, ClassroomClass) == 3
        with pytest.raises(classroom.ClassroomDenied):
            join(session, "ROTATED9")


def test_distinct_teaching_adults_count_once_and_roles_alone_do_not_count(s2_db):
    with s2_db() as session:
        institutional(session, teacher_limit=2)
        first, second = create(session, name="One"), create(session, name="Two")
        classroom.grant_role(session, actor=actor(session), program_id=1, verifier_id=4, role="billing")
        assignments = []
        for class_id, adult_id in ((first.id, 2), (second.id, 2), (first.id, 3)):
            assignments.append(service.assign_teacher(session, actor=actor(session), program_id=1,
                class_id=class_id, verifier_id=adult_id))
        with pytest.raises(classroom.ClassroomDenied):
            service.assign_teacher(session, actor=actor(session), program_id=1,
                class_id=second.id, verifier_id=1)
        service.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignments[0].id)
        with pytest.raises(classroom.ClassroomDenied):
            service.assign_teacher(session, actor=actor(session), program_id=1,
                class_id=second.id, verifier_id=1)
        service.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignments[1].id)
        service.assign_teacher(session, actor=actor(session), program_id=1, class_id=second.id, verifier_id=1)


def test_downgrade_preserves_data_blocks_paid_additions_and_allows_reduction(s2_db):
    with s2_db() as session:
        institutional(session, class_limit=3)
        first, second = create(session, name="One"), create(session, name="Two")
        code(session, first.id)
        member = join(session)
        institutional(session, class_limit=1)
        assert not service.entitlement_decision(session, 1).allowed
        with pytest.raises(classroom.ClassroomDenied):
            code(session, first.id, "ROTATED9")
        with pytest.raises(classroom.ClassroomDenied):
            create(session)
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=second.id, state="archived")
        assert service.entitlement_decision(session, 1).allowed
        assert session.get(ClassroomStudentMembership, member.id) is not None
        assert count(session, ClassroomClass) == 2


def test_archive_reactivation_rechecks_distinct_teaching_capacity(s2_db):
    with s2_db() as session:
        institutional(session, teacher_limit=1)
        first, second = create(session, name="One"), create(session, name="Two")
        service.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=first.id, verifier_id=2)
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=first.id, state="archived")
        replacement = service.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=second.id, verifier_id=3)
        with pytest.raises(classroom.ClassroomDenied):
            service.set_class_state(session, actor=actor(session), program_id=1, class_id=first.id, state="active")
        service.end_teaching(session, actor=actor(session), program_id=1, assignment_id=replacement.id)
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=first.id, state="active")
        assert count(session, ClassroomTeachingAssignment) == 2


@pytest.mark.parametrize("bad", ["", "A", "A" * 65, "BAND\x00", "BAND\n2026", "ＢＡＮＤ", "Straße", "CАFE2026", "BAND/2026"])
def test_codes_reject_unsupported_text(s2_db, bad):
    with s2_db() as session:
        trial(session)
        row = create(session)
        with pytest.raises(classroom.ClassroomDenied):
            code(session, row.id, bad)


def test_codes_canonical_parity_scope_collision_rotation_and_no_raw_audit(s2_db):
    with s2_db() as session:
        first, entry, visible = ready(session, preferred="  band2026  ")
        assert visible == "BAND2026"
        assert join(session, "  BaNd2026  ").class_id == first.id
        other = create(session, name="Another")
        with pytest.raises(classroom.ClassroomDenied):
            code(session, other.id, "BAND2026")
        session.commit()
        second, _, _ = ready(session, program_id=2)
        assert join(session, program_id=2).class_id == second.id
        session.commit()
        newer, _ = code(session, first.id, "ROTATED9")
        assert newer.generation > entry.generation
        with pytest.raises(classroom.ClassroomDenied):
            join(session, "BAND2026", 1)
        serialized = repr([vars(row) for row in session.scalars(select(ClassroomS2AuditEvent))])
        assert "BAND2026" not in serialized and "ROTATED9" not in serialized
        paid_code_audits = list(session.scalars(select(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action.in_(("code_issued", "code_rotated")))))
        assert paid_code_audits and all(event.entitlement_id for event in paid_code_audits)


def test_generated_code_collision_retries_without_extra_classes(s2_db, monkeypatch):
    with s2_db() as session:
        ready(session)
        other = create(session, name="Another")
        values = iter(["BAND2026", "FRESH123"])
        monkeypatch.setattr(service, "_generate_code", lambda: next(values))
        _, visible = code(session, other.id, None)
        assert visible == "FRESH123"
        assert count(session, ClassroomClass) == 2


def test_generated_collision_retry_is_bounded_and_keeps_existing_authority(s2_db, monkeypatch):
    with s2_db() as session:
        first, entry, _ = ready(session)
        attempts = []
        monkeypatch.setattr(service, "_generate_code", lambda: attempts.append(1) or "BAND2026")
        with pytest.raises(classroom.ClassroomDenied):
            code(session, first.id, None)
        assert 1 <= len(attempts) <= 16
        assert join(session).class_id == first.id


def test_registered_join_is_idempotent_multi_class_and_has_no_other_grants(s2_db):
    with s2_db() as session:
        first, _, _ = ready(session)
        before = {model: count(session, model) for model in (
            StudentVerifierConnection, StudentOrganizationMembership, TeamMembership, Enrollment,
            BillingAccount, Membership, MembershipSeat, ProfileCapability)}
        assert not student_has_full_access(session, 1)
        member = join(session)
        assert join(session).id == member.id
        second = create(session, name="Second Class")
        code(session, second.id, "SECOND88")
        assert join(session, "SECOND88").class_id == second.id
        assert len(periods(session, member.id)) == 1
        assert service.contribution_active(session, program_id=1, class_id=first.id, profile_id=1)
        assert not service.contribution_active(session, program_id=2, class_id=first.id, profile_id=1)
        assert not student_has_full_access(session, 1)
        assert all(count(session, model) == value for model, value in before.items())
        paid_audits = list(session.scalars(select(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action.in_(("class_activated", "code_issued", "student_joined")))))
        assert paid_audits and all(event.entitlement_id for event in paid_audits)
        with pytest.raises(classroom.ClassroomDenied, match="S3"):
            classroom.require_student_reporting(session, actor=actor(session), program_id=1, class_id=first.id)


@pytest.mark.parametrize("mode", ["closed", "archived", "expired", "ineligible"])
def test_join_revalidates_class_entitlement_and_account(s2_db, monkeypatch, mode):
    with s2_db() as session:
        row, _, _ = ready(session)
        if mode == "closed":
            service.set_enrollment(session, actor=actor(session), program_id=1, class_id=row.id, open=False)
        elif mode == "archived":
            service.set_class_state(session, actor=actor(session), program_id=1, class_id=row.id, state="archived")
        elif mode == "expired":
            monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(days=14))
        else:
            session.get(AccountPrivacy, 1).age_band = "unknown"
            session.flush()
        with pytest.raises(classroom.ClassroomDenied):
            join(session)
        assert count(session, ClassroomStudentMembership) == 0


def test_parent_account_approval_survives_rotated_entry_without_reverification(s2_db, monkeypatch):
    monkeypatch.setattr(child_authorization, "under13_available", lambda: True)
    monkeypatch.setattr(child_authorization, "notice_policy", lambda *args: ("synthetic-notice", "Synthetic", "a" * 64))
    with s2_db() as session:
        row, _, _ = ready(session)
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.public_from = "under13", None
        session.flush()
        intent = service.prepare_entry(session, student=student(session), program_id=1, code="BAND2026")
        assert not eligible(session, 1)
        with pytest.raises(classroom.ClassroomDenied):
            join(session, intent=intent)
        code(session, row.id, "ROTATED9")
        evidence = ConsentEvidence(profile_id=1, parent_email="parent@example.test",
            notice_version="synthetic-notice", notice_sha256="a" * 64, approved_at=NOW, confirmed_at=NOW)
        session.add(evidence)
        session.flush()
        rule.consent_id = evidence.id
        session.commit()
        assert eligible(session, 1)
        with pytest.raises(classroom.ClassroomDenied):
            join(session, intent=intent)
        assert eligible(session, 1)
        assert session.get(AccountPrivacy, 1).consent_id == evidence.id
        current = service.prepare_entry(session, student=student(session), program_id=1, code="ROTATED9")
        assert join(session, intent=current).profile_id == 1
        assert count(session, ConsentEvidence) == 1
        assert count(session, StudentVerifierConnection) == 0


def test_intent_is_account_program_bound_short_lived_and_not_tamperable(s2_db, monkeypatch):
    with s2_db() as session:
        ready(session)
        intent = service.prepare_entry(session, student=student(session), program_id=1, code="BAND2026")
        session.commit()
        trial(session, program_id=2)
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            join(session, program_id=2, intent=intent)
        session.rollback()
        with pytest.raises(classroom.ClassroomDenied):
            join(session, intent=intent + "tampered")
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(days=3))
        with pytest.raises(classroom.ClassroomDenied):
            join(session, intent=intent)
        assert count(session, ClassroomStudentMembership) == 0


def test_intent_reset_binding_cannot_reuse_old_account_authority(s2_db):
    with s2_db() as session:
        ready(session)
        intent = service.prepare_entry(session, student=student(session), program_id=1, code="BAND2026")
        session.commit()
        profile = session.get(WoodchuckProfile, 1)
        profile.session_version += 1
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            join(session, intent=intent)
        assert join(session).profile_id == 1


def test_intent_cannot_be_used_by_another_registered_account(s2_db):
    with s2_db() as session:
        ready(session)
        intent = service.prepare_entry(session, student=student(session), program_id=1, code="BAND2026")
        session.add(WoodchuckProfile(id=2, woodchuck_id="WC-SYNTHETIC2", display_name="Other Student",
            pin_hash=session.get(WoodchuckProfile, 1).pin_hash, instrument="Trumpet",
            level="Beginner", goal="Build daily consistency"))
        session.flush()
        session.add(AccountPrivacy(profile_id=2, age_band="13to17", declared_at=NOW, public_from=NOW))
        session.commit()
        other = service.authenticate_student(session, woodchuck_id="WC-SYNTHETIC2", pin="2468")
        with pytest.raises(classroom.ClassroomDenied):
            service.join_class(session, student=other, program_id=1, intent=intent)
        assert count(session, ClassroomStudentMembership) == 0


@pytest.mark.parametrize("remove", [False, True])
def test_leave_rejoin_hold_reinstate_preserves_nonoverlap_history(s2_db, monkeypatch, remove):
    with s2_db() as session:
        row, _, _ = ready(session)
        member = join(session)
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=1))
        service.leave_class(session, student=student(session), program_id=1, class_id=row.id)
        assert not service.contribution_active(session, program_id=1, class_id=row.id, profile_id=1)
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=2))
        assert join(session).id == member.id
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=3))
        service.suspend_member(session, actor=actor(session), program_id=1,
            class_id=row.id, membership_id=member.id,
            reason="admin_removal" if remove else "conduct", remove=remove)
        from app.classroom_models import ClassroomMembershipHold
        assert session.get(ClassroomMembershipHold, member.id).state == ("removed" if remove else "suspended")
        assert session.scalar(select(ClassroomS2AuditEvent.id).where(
            ClassroomS2AuditEvent.action == ("student_removed" if remove else "student_suspended")))
        with pytest.raises(classroom.ClassroomDenied):
            join(session)
        service.leave_class(session, student=student(session), program_id=1, class_id=row.id)
        with pytest.raises(classroom.ClassroomDenied):
            join(session)
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=4))
        service.reinstate_member(session, actor=actor(session), program_id=1,
            class_id=row.id, membership_id=member.id)
        history = periods(session, member.id)
        assert len(history) >= 3 and sum(item.ended_at is None for item in history) == 1
        for prior, following in zip(history, history[1:]):
            assert prior.ended_at is not None and utc(prior.ended_at) <= utc(following.starts_at)
        assert service.contribution_active(session, program_id=1, class_id=row.id, profile_id=1)
        reinstated = session.scalar(select(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action == "student_reinstated"))
        assert reinstated.entitlement_id


def test_archive_and_expiry_preserve_membership_records(s2_db, monkeypatch):
    with s2_db() as session:
        row, _, _ = ready(session)
        member = join(session)
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=row.id, state="archived")
        assert not service.contribution_active(session, program_id=1, class_id=row.id, profile_id=1)
        assert len(periods(session, member.id)) == 1
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=row.id, state="active")
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(days=14))
        assert not service.contribution_active(session, program_id=1, class_id=row.id, profile_id=1)
        assert count(session, ClassroomStudentMembership) == count(session, ClassroomMembershipPeriod) == 1


@pytest.mark.parametrize("role", ["billing", "code_manager", "teacher"])
def test_nonadmin_roles_cannot_manage_students_or_create_classes(s2_db, role):
    with s2_db() as session:
        row, _, _ = ready(session)
        member = join(session)
        if role == "teacher":
            service.assign_teacher(session, actor=actor(session), program_id=1,
                                   class_id=row.id, verifier_id=2)
        else:
            classroom.grant_role(session, actor=actor(session), program_id=1,
                                 verifier_id=2, role=role)
        with pytest.raises(classroom.ClassroomDenied):
            create(session, adult_id=2)
        with pytest.raises(classroom.ClassroomDenied):
            service.suspend_member(session, actor=actor(session, 2), program_id=1,
                class_id=row.id, membership_id=member.id, reason="conduct")
        with pytest.raises(classroom.ClassroomDenied):
            classroom.grant_role(session, actor=actor(session, 2), program_id=1,
                                 verifier_id=2, role="admin")
        if role == "code_manager":
            assert code(session, row.id, "ROTATED9", adult_id=2)[1] == "ROTATED9"
        else:
            with pytest.raises(classroom.ClassroomDenied):
                code(session, row.id, "ROTATED9", adult_id=2)


def test_admin_allowed_lifecycle_student_and_cross_program_authority_denied(s2_db, monkeypatch):
    with s2_db() as session:
        row, _, _ = ready(session)
        member = join(session)
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=1))
        classroom.grant_role(session, actor=actor(session), program_id=1, verifier_id=2, role="admin")
        service.set_enrollment(session, actor=actor(session, 2), program_id=1, class_id=row.id, open=False)
        service.suspend_member(session, actor=actor(session, 2), program_id=1,
            class_id=row.id, membership_id=member.id, reason="conduct")
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            service.set_enrollment(session, actor=actor(session, 2), program_id=2, class_id=row.id, open=True)
        session.rollback()
        with pytest.raises(classroom.ClassroomDenied):
            service.set_enrollment(session, actor=student(session), program_id=1, class_id=row.id, open=True)


def test_membership_audit_rolls_back_atomically(s2_db):
    with s2_db() as session:
        ready(session)
        session.commit()
        baseline = count(session, ClassroomS2AuditEvent)
        join(session)
        assert count(session, ClassroomS2AuditEvent) > baseline
        session.rollback()
        assert count(session, ClassroomS2AuditEvent) == baseline
        assert count(session, ClassroomStudentMembership) == count(session, ClassroomMembershipPeriod) == 0


def test_database_rejects_second_open_period(s2_db):
    with s2_db() as session:
        ready(session)
        member = join(session)
        session.commit()
        with pytest.raises(IntegrityError):
            session.add(ClassroomMembershipPeriod(membership_id=member.id,
                starts_at=NOW + timedelta(minutes=1), state="active"))
            session.flush()
        session.rollback()


def assert_corrupt_history_refused(factory, shape, operation):
    # Deliberately corrupt metadata fixtures exercise every writer's defensive
    # check. A correctly migrated c23 refuses these histories at the DB boundary.
    def snapshot(session):
        return {table.name: list(session.execute(select(table).order_by(*table.primary_key.columns)))
                for table in Base.metadata.sorted_tables if table.name.startswith("classroom_")}

    with factory() as session:
        row, _, _ = ready(session)
        member = ClassroomStudentMembership(class_id=row.id, profile_id=1)
        session.add(member)
        session.flush()
        for start, end in ((40, 20), (30, None if shape == "closed_open" else 10)):
            session.add(ClassroomMembershipPeriod(membership_id=member.id,
                starts_at=NOW - timedelta(minutes=start),
                ended_at=NOW - timedelta(minutes=end) if end is not None else None,
                state="active", ended_reason="departed" if end is not None else None))
        if operation == "reinstate":
            session.add(ClassroomMembershipHold(membership_id=member.id, state="suspended",
                reason="conduct", held_by_verifier_id=1, held_at=NOW - timedelta(minutes=5)))
        session.commit()
        before = snapshot(session)
        with pytest.raises(classroom.ClassroomDenied, match="overlap"):
            if operation == "join":
                join(session)
            elif operation == "leave":
                service.leave_class(session, student=student(session), program_id=1, class_id=row.id)
            elif operation == "reinstate":
                service.reinstate_member(session, actor=actor(session), program_id=1,
                    class_id=row.id, membership_id=member.id)
            else:
                service.suspend_member(session, actor=actor(session), program_id=1,
                    class_id=row.id, membership_id=member.id, remove=operation == "remove",
                    reason="admin_removal" if operation == "remove" else "conduct")
        session.commit()
        session.expire_all()
        assert snapshot(session) == before


@pytest.mark.parametrize("shape", ["closed_closed", "closed_open"])
@pytest.mark.parametrize("operation", ["join", "leave", "suspend", "remove", "reinstate"])
def test_all_membership_writers_refuse_corrupt_history_without_changes(s2_db, shape, operation):
    assert_corrupt_history_refused(s2_db, shape, operation)


def test_connected_student_relationship_does_not_grant_classroom_administration(s2_db):
    with s2_db() as session:
        row, _, _ = ready(session)
        member = join(session)
        session.add(StudentVerifierConnection(profile_id=1, verifier_id=2,
            role="band_director", status="accepted"))
        session.flush()
        with pytest.raises(classroom.ClassroomDenied):
            create(session, adult_id=2)
        with pytest.raises(classroom.ClassroomDenied):
            service.suspend_member(session, actor=actor(session, 2), program_id=1,
                class_id=row.id, membership_id=member.id, reason="conduct")


def test_legacy_held_period_cannot_leave_then_reenter_without_reinstatement(s2_db):
    with s2_db() as session:
        row, _, _ = ready(session)
        member = ClassroomStudentMembership(class_id=row.id, profile_id=1)
        session.add(member)
        session.flush()
        session.add(ClassroomMembershipPeriod(membership_id=member.id,
            starts_at=NOW - timedelta(days=1), state="held"))
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            service.leave_class(session, student=student(session), program_id=1, class_id=row.id)
        with pytest.raises(classroom.ClassroomDenied):
            join(session)
        service.reinstate_member(session, actor=actor(session), program_id=1,
            class_id=row.id, membership_id=member.id)
        assert service.contribution_active(session, program_id=1, class_id=row.id, profile_id=1)
        assert [period.state for period in periods(session, member.id)] == ["held", "active"]


@pytest.mark.parametrize("collision", ["class", "program_digest"])
def test_database_uniqueness_backs_current_code_invariants(s2_db, collision):
    with s2_db() as session:
        row, entry, _ = ready(session)
        other = create(session, name="Other Class")
        class_id = row.id if collision == "class" else other.id
        digest = "b" * 64 if collision == "class" else entry.digest
        generation = 2 if collision == "class" else 1
        session.commit()
        with pytest.raises(IntegrityError):
            session.add(ClassroomEntryCode(program_id=1, class_id=class_id, digest=digest,
                generation=generation, is_current=True, issued_by_verifier_id=1, issued_at=NOW))
            session.flush()
        session.rollback()
        assert session.scalar(select(func.count()).select_from(ClassroomEntryCode).where(
            ClassroomEntryCode.is_current.is_(True))) == 1


def test_suspension_survives_archive_reactivation_and_fresh_code(s2_db, monkeypatch):
    with s2_db() as session:
        row, _, _ = ready(session)
        member = join(session)
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=1))
        service.suspend_member(session, actor=actor(session), program_id=1,
            class_id=row.id, membership_id=member.id, reason="conduct")
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=row.id, state="archived")
        service.set_class_state(session, actor=actor(session), program_id=1, class_id=row.id, state="active")
        code(session, row.id, "ROTATED9")
        service.set_enrollment(session, actor=actor(session), program_id=1, class_id=row.id, open=True)
        with pytest.raises(classroom.ClassroomDenied):
            join(session, "ROTATED9")
        assert not service.contribution_active(session, program_id=1, class_id=row.id, profile_id=1)
