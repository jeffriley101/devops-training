from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.account_deletion import anonymize_woodchuck_account
from app.age_models import AccountPrivacy, AgeCorrection
from app.age_privacy import correct_age, declare_age
from app.child_models import ConsentEvidence
from app.classroom_enrollment_models import ClassroomEnrollmentInvitation
from app.classroom_models import ClassroomClass, ClassroomProgram, ClassroomStudentMembership, ClassroomTeachingAssignment
from app.deletion_age_history import age_history_complete
from app.main import app
from app.models import Organization, TrustedVerifier, WoodchuckProfile
from tests.test_account_deletion import NOW, profile


def test_authenticated_deletion_removes_standalone_age_declaration(play_pg):
    with play_pg.begin() as session:
        subject = profile(session, 1)
        other = profile(session, 2)
        declare_age(session, subject.id, "under13", at=NOW)
        other_rule = declare_age(session, other.id, "13to17", at=NOW)
        other_rule.private_plunge_best = 15
        assert not age_history_complete(session, subject.id)
    credentials = {"woodchuck_id": subject.woodchuck_id, "pin": "1234"}
    with TestClient(app) as client:
        assert client.post("/account/login", data=credentials).status_code == 200
        assert client.post("/account/delete", data={**credentials, "confirmation": "no"}).status_code == 400
        with play_pg() as session:
            assert session.get(AccountPrivacy, subject.id).age_band == "under13"
        assert client.post("/account/delete", data={**credentials, "confirmation": "DELETE"},
                           follow_redirects=False).status_code == 303
    with play_pg() as session:
        assert session.get(WoodchuckProfile, subject.id).status == "deleted"
        assert session.get(AccountPrivacy, subject.id) is None
        assert age_history_complete(session, subject.id)
        other_rule = session.get(AccountPrivacy, other.id)
        assert (other_rule.age_band, other_rule.declared_at, other_rule.private_plunge_best) == ("13to17", NOW, 15)


def test_stale_age_declaration_and_correction_cannot_recreate_deleted_history(play_pg):
    with play_pg.begin() as session:
        student = profile(session, 1)
        declare_age(session, student.id, "unknown", at=NOW)
    with play_pg() as stale:
        target = stale.get(WoodchuckProfile, student.id)
        rule = stale.get(AccountPrivacy, student.id)
        assert target.status == "active" and rule.age_band == "unknown"
        with play_pg.begin() as session:
            anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id),
                                        now=datetime.now(timezone.utc))
        with pytest.raises(ValueError, match="Student sign-in"):
            declare_age(stale, student.id, "adult")
        with pytest.raises(ValueError, match="active, restricted declaration"):
            correct_age(stale, student.id, "adult", expected_band="unknown", expected_declared_at=NOW.isoformat(),
                        actor="a" * 64, case_reference="review-023")
        stale.commit()
    with play_pg() as session:
        assert session.get(AccountPrivacy, student.id) is None
        assert session.scalar(select(AgeCorrection.id).where(AgeCorrection.profile_id == student.id)) is None


@pytest.mark.parametrize("dependency", ["correction", "consent", "classroom", "enrollment_offer"])
def test_dispose_private_score_preserves_age_evidence(play_pg, dependency):
    with play_pg.begin() as session:
        student = profile(session, 1)
        student.plunge_best_score = 52
        session.flush()
        band = "13to17" if dependency == "enrollment_offer" else "under13"
        rule = declare_age(session, student.id, band, at=NOW)
        if dependency == "correction":
            correct_age(session, student.id, "13to17", expected_band="under13",
                        expected_declared_at=NOW.isoformat(), actor="a" * 64, case_reference="age-case-123")
            evidence = AgeCorrection
        elif dependency == "consent":
            record = ConsentEvidence(profile_id=student.id, parent_email="synthetic@example.test",
                                     notice_version="verified-notice", notice_sha256="a" * 64,
                                     approved_at=NOW, confirmed_at=NOW)
            session.add(record)
            session.flush()
            rule.consent_id = record.id
            evidence = ConsentEvidence
        else:
            org = Organization(name="Test School", organization_type="school")
            owner = TrustedVerifier(email="authorized@example.test", display_name="School owner",
                                    pin_hash=student.pin_hash)
            session.add_all([org, owner])
            session.flush()
            session.add(ClassroomProgram(organization_id=org.id, owner_verifier_id=owner.id))
            session.flush()
            classroom = ClassroomClass(program_id=org.id, display_name="Test Class")
            session.add(classroom)
            session.flush()
            if dependency == "enrollment_offer":
                issued = datetime.now(timezone.utc)
                teacher = ClassroomTeachingAssignment(program_id=org.id, class_id=classroom.id, verifier_id=owner.id,
                                                       starts_at=NOW, granted_by_verifier_id=owner.id)
                session.add(teacher)
                session.flush()
                session.add(ClassroomEnrollmentInvitation(purpose="classroom-enrollment-v2", profile_id=student.id,
                    program_id=org.id, class_id=classroom.id, assignment_id=teacher.id, issued_by_verifier_id=owner.id,
                    issued_at=issued, expires_at=issued + timedelta(days=7), state="pending"))
                assert session.scalar(select(ClassroomStudentMembership.id)) is None
                evidence = ClassroomEnrollmentInvitation
            else:
                session.add(ClassroomStudentMembership(profile_id=student.id, class_id=classroom.id))
                evidence = ClassroomStudentMembership
        session.flush()
        original = (rule.profile_id, rule.age_band, rule.declared_at, rule.public_from, rule.consent_id)
        statement = select(evidence.__table__).where(evidence.profile_id == student.id)
        initial_rows = session.execute(statement).all()
        assert len(initial_rows) == 1
        assert rule.private_plunge_best == 52
    with play_pg.begin() as session:
        anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id),
                                    now=datetime.now(timezone.utc))
    with play_pg() as session:
        final = session.get(AccountPrivacy, student.id)
        assert (final.profile_id, final.age_band, final.declared_at, final.public_from, final.consent_id) == original
        assert final.private_plunge_best == 0
        assert session.execute(statement).all() == initial_rows
        assert age_history_complete(session, student.id)
