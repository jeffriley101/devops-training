"""Internal eligibility never grants access to P-Chart data."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import classroom, classroom_chart_eligibility as eligibility, classroom_reporting, classroom_s2 as s2
from app.age_models import AccountPrivacy
from app.classroom_models import ClassroomChartShare, ClassroomMembershipPeriod, ClassroomReportingPeriod
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW, ready, join, student, create


@pytest.fixture
def enabled(s2_db, monkeypatch):
    monkeypatch.setenv("CLASSROOM_DIRECTOR_ENABLED", "1")
    monkeypatch.setattr(classroom_reporting, "_now", lambda: NOW)
    return s2_db


def candidate(session, class_id, assignment_id, **kwargs):
    return eligibility.enrolled_recipient(session, student=student(session),
        program_id=kwargs.get("program_id", 1), class_id=class_id, assignment_id=assignment_id)


def test_enrollment_and_exact_teacher_are_required(enabled):
    with enabled() as session:
        room, _, _ = ready(session)
        assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
                                       class_id=room.id, verifier_id=2)
        # A generic Class entry code is not a student-specific invitation.
        with pytest.raises(classroom.ClassroomDenied):
            candidate(session, room.id, assignment.id)
        member = join(session)
        decision = candidate(session, room.id, assignment.id)
        assert (decision.profile_id, decision.verifier_id, decision.class_id) == (1, 2, room.id)
        assert session.get(ClassroomMembershipPeriod, decision.membership_period_id).membership_id == member.id
        other = create(session, name="Other Class")
        for class_id, assignment_id, program_id in [(other.id, assignment.id, 1),
                (room.id, assignment.id, 2), (room.id, 999999, 1)]:
            with pytest.raises(classroom.ClassroomDenied):
                candidate(session, class_id, assignment_id, program_id=program_id)
        assert session.scalar(select(func.count()).select_from(ClassroomChartShare)) == 0
        assert session.scalar(select(func.count()).select_from(ClassroomReportingPeriod)) == 0


def test_gate_assignment_and_membership_changes_revoke_eligibility(enabled, monkeypatch):
    with enabled() as session:
        room, _, _ = ready(session)
        member = join(session)
        assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
                                       class_id=room.id, verifier_id=2)
        assert candidate(session, room.id, assignment.id)
        monkeypatch.delenv("CLASSROOM_DIRECTOR_ENABLED")
        with pytest.raises(classroom.ClassroomDenied):
            candidate(session, room.id, assignment.id)
        monkeypatch.setenv("CLASSROOM_DIRECTOR_ENABLED", "1")
        s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignment.id)
        with pytest.raises(classroom.ClassroomDenied):
            candidate(session, room.id, assignment.id)
        replacement = s2.assign_teacher(session, actor=actor(session), program_id=1,
                                        class_id=room.id, verifier_id=2)
        monkeypatch.setattr(s2, "_now", lambda: NOW + timedelta(seconds=1))
        monkeypatch.setattr(classroom_reporting, "_now", lambda: NOW + timedelta(seconds=1))
        s2.suspend_member(session, actor=actor(session), program_id=1, class_id=room.id,
                          membership_id=member.id, reason="conduct")
        with pytest.raises(classroom.ClassroomDenied):
            candidate(session, room.id, replacement.id)


def test_expired_entitlement_and_forged_student_proof_denied(enabled, monkeypatch):
    with enabled() as session:
        room, _, _ = ready(session)
        join(session)
        assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
                                       class_id=room.id, verifier_id=2)
        with pytest.raises(classroom.ClassroomDenied):
            eligibility.enrolled_recipient(session, student=1, program_id=1,
                                          class_id=room.id, assignment_id=assignment.id)
        monkeypatch.setattr(s2, "_now", lambda: NOW + timedelta(days=90))
        monkeypatch.setattr(classroom_reporting, "_now", lambda: NOW + timedelta(days=90))
        with pytest.raises(classroom.ClassroomDenied):
            candidate(session, room.id, assignment.id)


@pytest.mark.parametrize("age_band", ["under13", "unknown"])
def test_restricted_accounts_are_denied(enabled, age_band):
    with enabled() as session:
        room, _, _ = ready(session)
        join(session)
        assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
                                       class_id=room.id, verifier_id=2)
        session.get(AccountPrivacy, 1).age_band = age_band
        session.flush()
        with pytest.raises(classroom.ClassroomDenied):
            candidate(session, room.id, assignment.id)
