"""Verify the deletion executor's existing school-authority boundary on PostgreSQL."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from app import classroom, classroom_s2, classroom_school_authorization as school
from app.classroom_invitation import create_program_invitation
from app.classroom_school_models import ClassroomSchoolAuthorization
from app.models import Organization
from tests.test_adult_deletion_support import adult_case, records
from tests.test_classroom_s2 import admin
from tests.test_classroom_school_authorization import simulated_legal_review


@pytest.mark.parametrize("approved_agreement", [False, True], ids=["draft", "simulated-approved"])
def test_deleting_approver_preserves_independent_school_receipt_and_real_end(
    play_pg, adult_case, monkeypatch, approved_agreement
):
    case = adult_case
    if approved_agreement:
        simulated_legal_review(monkeypatch)
    expected_status = "authorized" if approved_agreement else "legal_review_pending"
    for flag in ("CLASSROOM_S1_ENABLED", "CLASSROOM_S2_ENABLED", "CLASSROOM_SCHOOL_AUTHORIZATION_ENABLED"):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "synthetic-classroom-s2-admin")
    verified_at = case.now - timedelta(minutes=30)
    monkeypatch.setattr(classroom_s2, "_now", lambda: verified_at)
    with play_pg.begin() as db:
        org = Organization(name="Synthetic School", organization_type="school")
        db.add(org)
        db.flush()
        token = create_program_invitation(org.id, case.colleague.email)
        classroom.accept_program_invitation(db, token=token, pin="2468")
        owner = classroom.authenticate_adult(db, email=case.colleague.email, pin="2468")
        approval = school.verify_authority(
            db, admin=admin(), reviewer=owner, program_id=org.id, approver_id=case.adult.id,
            authority_kind="director", school_name=org.name, authority_description="Annual Program approval",
            contact_name="Synthetic school office", contact_role="Registrar", contact_address="office@school.test",
            contact_source="https://school.test/directory", verification_method="official_email",
            verification_notes="The office confirmed authority for this specific Program.",
            contacted_at=verified_at - timedelta(minutes=5), year_start=school.school_year(verified_at),
            independently_confirmed=True, previous=(None, None, None, None),
        )
        approver = classroom.authenticate_adult(db, email=case.adult.email, pin="1357")
        school.approve(db, actor=approver, program_id=org.id, record_id=approval.id,
                      previous=school.revision(approval), agreement_version=school.AGREEMENT_VERSION,
                      agreement_digest=school.AGREEMENT_DIGEST, explicit=True)
        assert school.status(approval, case.now) == expected_status
        assert school.authorized_program(db, program_id=org.id) is approved_agreement

    before = records(play_pg, ClassroomSchoolAuthorization)
    receipt = case.run()
    assert receipt["status"] == "adult_deletion_applied"
    assert records(play_pg, ClassroomSchoolAuthorization) == before
    with play_pg.begin() as db:
        with pytest.raises(classroom.ClassroomDenied):
            classroom.authenticate_adult(db, email=case.adult.email, pin="1357")
        original = db.get(ClassroomSchoolAuthorization, approval.id)
        assert school.status(original, case.now) == expected_status
        assert school.authorized_program(db, program_id=org.id) is approved_agreement
        assert school.status(original, original.expires_at) == "expired"
        assert original.revoked_at is None
        # The remaining authorized owner can subsequently end this actual grant.
        ended_at = case.now + timedelta(minutes=3)
        monkeypatch.setattr(classroom_s2, "_now", lambda: ended_at)
        owner = classroom.authenticate_adult(db, email=case.colleague.email, pin="2468")
        school.revoke(db, actor=owner, program_id=org.id, record_id=original.id,
                     previous=school.revision(original), reason="authority_revoked")
        assert (original.revoked_at, original.revoked_by_id, original.revocation_reason) == (
            ended_at, case.colleague.id, "authority_revoked")
        assert original.evidence_digest == school._evidence_digest(original)
        assert not school.authorized_program(db, program_id=org.id)
    assert case.run(status_only=True, clock=lambda: ended_at + timedelta(days=31)) == receipt
    with case.engine.connect() as restricted:
        assert restricted.execute(text(
            "SELECT has_column_privilege('classroom_school_authorizations', 'verification_notes', 'UPDATE'), "
            "has_table_privilege('classroom_school_authorizations', 'DELETE'), "
            "has_column_privilege('classroom_role_grants', 'ended_at', 'UPDATE'), "
            "has_column_privilege('classroom_teaching_assignments', 'ended_at', 'UPDATE')"
        )).one() == (False, False, False, False)
