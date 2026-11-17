"""Dispose of one locked student's age declaration when it has no remaining evidence use."""
from sqlalchemy import and_, delete, or_, select, update

from .age_models import AccountPrivacy, AgeCorrection
from .child_models import ConsentEvidence, ParentAgeDeclaration
from .classroom_enrollment_models import ClassroomEnrollmentInvitation
from .classroom_models import ClassroomStudentInvitation, ClassroomStudentMembership
from .models import CampPointAward, ContestResult, PracticeChart, Team, TeamMembership, TeamWeekMembershipSnapshot


def _standalone_declaration(profile_id):
    # These consent and school records can depend on the original declaration
    # or publication clock. A privileged correction has a separate case audit.
    # Team and instrument readers still use the rule for historical contributors.
    evidence_subjects = (
        ConsentEvidence.profile_id, ParentAgeDeclaration.profile_id, AgeCorrection.profile_id,
        ClassroomStudentMembership.profile_id, ClassroomStudentInvitation.profile_id, ClassroomEnrollmentInvitation.profile_id,
        PracticeChart.profile_id, Team.creator_profile_id, TeamMembership.profile_id,
        TeamWeekMembershipSnapshot.profile_id, ContestResult.profile_id, CampPointAward.profile_id,
    )
    return and_(
        AccountPrivacy.consent_id.is_(None),
        *(~select(1).where(subject == profile_id).exists() for subject in evidence_subjects),
    )


def clear_age_history(session, profile_id):
    subject = AccountPrivacy.profile_id == profile_id
    session.execute(delete(AccountPrivacy).where(subject, _standalone_declaration(profile_id))
                    .execution_options(synchronize_session=False))
    # This private game-score copy has no authority or scoring reader.
    session.execute(update(AccountPrivacy).where(subject, AccountPrivacy.private_plunge_best != 0)
                    .values(private_plunge_best=0).execution_options(synchronize_session=False))


def age_history_complete(session, profile_id):
    residue = select(1).where(
        AccountPrivacy.profile_id == profile_id,
        or_(AccountPrivacy.private_plunge_best != 0, _standalone_declaration(profile_id)),
    )
    return not session.scalar(select(residue.exists()))
