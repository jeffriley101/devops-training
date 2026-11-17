"""Account-scoped private activity disposal. The caller verifies and locks the subject."""
from sqlalchemy import String, and_, cast, delete, func, literal, or_, select, update

from .classroom_guardian_models import ClassroomGuardianAuthorization
from .classroom_models import (
    ClassroomChartDirectorShare, ClassroomChartShare, ClassroomProgressAuthorization,
    ClassroomReportingPeriod,
)
from .models import (
    ArcadeAttemptPack, ArcadeHighScore, ArcadePlaySession, ArcadeStartRequest, CampPointAward,
    CrownAward, DailyTriviaAttempt, LoginStreak, OwnedItemCopy, PlungePointAward, PracticeChart,
    PracticeChartVerification, QuestCompletion, RewardGrant, RewardInventoryPlacement,
    TravelingCupPlacement, WoodchuckProfile,
)
from .weekly_models import (
    WeeklyInstrumentPlan, WeeklyNewsletterPresentation, WeeklyPlan, WeeklyTaskClaim, WeeklyWheelPlay,
)


def _matching(*conditions):
    return select(1).where(*conditions).exists()


def _private_activity(profile_id):
    # Both ends of legacy Arcade links carry an owner independently. Keep an
    # inconsistent link and either endpoint that would cascade onto another owner.
    owned_packs = select(ArcadeAttemptPack.id).where(ArcadeAttemptPack.profile_id == profile_id)
    play = and_(
        ArcadePlaySession.profile_id == profile_id,
        or_(ArcadePlaySession.pack_id.is_(None), ArcadePlaySession.pack_id.in_(owned_packs)),
        ~_matching(ArcadeStartRequest.play_id == ArcadePlaySession.id,
                   ArcadeStartRequest.profile_id != profile_id),
    )
    owned_plays = select(ArcadePlaySession.id).where(play).correlate(None)
    pack = and_(ArcadeAttemptPack.profile_id == profile_id,
                ~_matching(ArcadePlaySession.pack_id == ArcadeAttemptPack.id))
    placement = and_(
        RewardInventoryPlacement.profile_id == profile_id,
        or_(RewardInventoryPlacement.crown_award_id.in_(
                select(CrownAward.id).where(CrownAward.profile_id == profile_id)),
            RewardInventoryPlacement.reward_grant_id.in_(
                select(RewardGrant.id).where(RewardGrant.profile_id == profile_id))),
    )
    # A Wheel play is original evidence for a wheel-prize Camp Point. Preserve
    # even a conflicting award reference for review. Other Wheel outcomes and
    # the virtual payment have self-contained RewardGrant receipts.
    source_prefix = literal("weekly-wheel:") + WeeklyWheelPlay.id + ":"
    wheel = and_(WeeklyWheelPlay.profile_id == profile_id, ~_matching(
        func.substr(CampPointAward.duplicate_key, 1, func.length(source_prefix)) == source_prefix,
    ))
    return [
        (ArcadeStartRequest, and_(ArcadeStartRequest.profile_id == profile_id,
                                  ArcadeStartRequest.play_id.in_(owned_plays))),
        (ArcadePlaySession, play), (ArcadeAttemptPack, pack),
        (ArcadeHighScore, ArcadeHighScore.profile_id == profile_id),
        (PlungePointAward, PlungePointAward.profile_id == profile_id),
        (DailyTriviaAttempt, DailyTriviaAttempt.profile_id == profile_id),
        (LoginStreak, LoginStreak.profile_id == profile_id),
        (QuestCompletion, QuestCompletion.profile_id == profile_id),
        (WeeklyNewsletterPresentation, WeeklyNewsletterPresentation.profile_id == profile_id),
        (WeeklyWheelPlay, wheel),
        (OwnedItemCopy, OwnedItemCopy.profile_id == profile_id),
        (RewardInventoryPlacement, placement),
        (TravelingCupPlacement, TravelingCupPlacement.profile_id == profile_id),
    ]


def _outside_school_reporting(profile_id, timestamp):
    # The 41-field reader needs chart creation and plan issuance within an
    # actual authorization interval. Keep possible matches even if other
    # eligibility gates currently prevent display. Never infer an end date.
    period = ClassroomReportingPeriod
    student = ClassroomProgressAuthorization
    guardian = ClassroomGuardianAuthorization
    return and_(
        ~_matching(period.profile_id == profile_id, period.starts_at <= timestamp,
                   or_(period.ended_at.is_(None), period.ended_at >= timestamp)),
        ~_matching(student.profile_id == profile_id, student.accepted_at <= timestamp,
                   or_(student.withdrawn_at.is_(None), student.withdrawn_at >= timestamp)),
        ~_matching(guardian.profile_id == profile_id, guardian.purpose == "reporting",
                   guardian.accepted_at <= timestamp, guardian.expires_at >= timestamp,
                   or_(guardian.withdrawn_at.is_(None), guardian.withdrawn_at >= timestamp)),
    )


def _private_school_activity(profile_id):
    chart = PracticeChart
    private_chart = and_(
        chart.profile_id == profile_id, chart.source.in_(("p-book", "pristine")),
        chart.include_contests.is_(False), chart.include_team_contests.is_(False),
        chart.team_id.is_(None), chart.credits_awarded == 0,
        ~_matching(PracticeChartVerification.practice_chart_id == chart.id),
        ~_matching(ClassroomChartShare.chart_id == chart.id),
        ~_matching(ClassroomChartDirectorShare.chart_id == chart.id),
        ~_matching(RewardGrant.source_key == literal("practice-chart:") + cast(chart.id, String)),
        _outside_school_reporting(profile_id, chart.created_at),
    )
    # Instrument plans and non-currency claims belong solely to their plan.
    # Existing rewards carry a separate receipt and are never cascaded.
    plan = and_(WeeklyPlan.profile_id == profile_id, _outside_school_reporting(profile_id, WeeklyPlan.issued_at))
    return [(chart, private_chart), (WeeklyPlan, plan)]


def _delete_rows(session, rows):
    for model, predicate in rows:
        session.execute(delete(model).where(predicate).execution_options(synchronize_session=False))


def _rows_absent(session, rows):
    return all(not session.scalar(select(_matching(predicate))) for _, predicate in rows)


def clear_private_activity(session, profile_id):
    _delete_rows(session, _private_activity(profile_id))
    session.execute(update(WoodchuckProfile).where(
        WoodchuckProfile.id == profile_id, WoodchuckProfile.plunge_best_score != 0,
    ).values(plunge_best_score=0, updated_at=WoodchuckProfile.updated_at))


def private_activity_complete(session, profile_id):
    best = session.scalar(select(WoodchuckProfile.plunge_best_score).where(WoodchuckProfile.id == profile_id))
    return best == 0 and _rows_absent(session, _private_activity(profile_id))


def clear_private_school_activity(session, profile_id):
    rows = _private_school_activity(profile_id)
    if session.get_bind().dialect.name == "sqlite":
        # Existing SQLite databases can have foreign key enforcement disabled.
        plan_ids = select(WeeklyPlan.id).where(rows[-1][1])
        for child in (WeeklyInstrumentPlan, WeeklyTaskClaim):
            session.execute(delete(child).where(child.plan_id.in_(plan_ids)).execution_options(synchronize_session=False))
    _delete_rows(session, rows)


def private_school_activity_complete(session, profile_id):
    return _rows_absent(session, _private_school_activity(profile_id))
