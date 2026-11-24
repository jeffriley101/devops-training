"""Targeted acceptance through the student route on migrated, disposable PostgreSQL."""
from contextlib import closing

import pytest
from sqlalchemy import select

from app import classroom
from app.child_models import ConsentEvidence
from app.classroom_enrollment_models import ClassroomEnrollmentAuditEvent, ClassroomEnrollmentInvitation
from app.classroom_guardian_models import ClassroomGuardianAuthorization, ClassroomGuardianRecipient
from app.classroom_invitation import create_program_invitation
from app.classroom_models import (
    ClassroomChartShare, ClassroomMembershipPeriod, ClassroomProgressRecipient,
    ClassroomS2AuditEvent, ClassroomStudentMembership,
)
from app.memberships import student_has_full_access
from app.models import Organization, TrustedVerifier
from test_classroom_enrollment_ui import PATH, enrollment_web, invite, respond, student_client
from test_classroom_enrollment_v2 import PROTECTED
from test_classroom_s1 import adult_pin_hash, count
from test_classroom_s2_postgres import s2_pg as s2_db  # Database dependency of enrollment_web.


@pytest.fixture
def classroom_pg(play_pg, monkeypatch, adult_pin_hash):
    """Supply both Programs for the shared S2 and authenticated web fixtures."""
    monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-classroom-s2-only-secret")
    with play_pg.begin() as session:
        session.add_all(TrustedVerifier(
            email=f"adult{n}@example.test", display_name=f"Adult {n}", pin_hash=adult_pin_hash,
        ) for n in range(1, 5))
        session.add_all(Organization(
            id=n, name=f"Program {n}", organization_type="music",
        ) for n in (1, 2))
        session.flush()
        for program_id in (1, 2):
            token = create_program_invitation(program_id, "adult1@example.test")
            classroom.accept_program_invitation(session, token=token, pin="2468")
    return play_pg


def test_first_targeted_acceptance_commits_one_period_and_retries(enrollment_web):
    refs = enrollment_web
    invitation_id = invite(refs)
    with refs.factory() as session:
        assert session.autoflush is False
        assert count(session, ClassroomStudentMembership) == 0
        assert count(session, ClassroomMembershipPeriod) == 0

    with closing(student_client()) as client:
        for _ in range(2):
            page = respond(client, invitation_id, "accept")
            assert page.status_code == 200
            assert "Enrollment confirmed." in page.text
        memberships = client.get(PATH).context["members"]
        assert [(row["program_id"], row["class_id"]) for row in memberships] == [(1, refs.class_id)]

    # Force an independent PostgreSQL connection after both route transactions committed.
    refs.factory.kw["bind"].dispose()
    with refs.factory() as session:
        membership = session.scalars(select(ClassroomStudentMembership)).one()
        period = session.scalars(select(ClassroomMembershipPeriod)).one()
        invitation = session.get(ClassroomEnrollmentInvitation, invitation_id)
        assert (membership.profile_id, membership.class_id) == (1, refs.class_id)
        assert (period.membership_id, period.state, period.ended_at) == (membership.id, "active", None)
        assert (invitation.state, invitation.accepted_period_id) == ("accepted", period.id)
        events = session.execute(select(
            ClassroomEnrollmentAuditEvent.action, ClassroomEnrollmentAuditEvent.membership_period_id,
        ).where(ClassroomEnrollmentAuditEvent.invitation_id == invitation_id)
          .order_by(ClassroomEnrollmentAuditEvent.id)).all()
        assert events == [("issued", None), ("accepted", period.id)]
        joined = session.scalars(select(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action == "student_joined")).one()
        assert (joined.program_id, joined.class_id, joined.profile_id, joined.membership_id) == (
            1, refs.class_id, membership.profile_id, membership.id)
        for model in (*PROTECTED, ClassroomChartShare, ClassroomProgressRecipient,
                      ConsentEvidence, ClassroomGuardianAuthorization, ClassroomGuardianRecipient):
            assert count(session, model) == 0, model.__tablename__
        assert not student_has_full_access(session, membership.profile_id)
