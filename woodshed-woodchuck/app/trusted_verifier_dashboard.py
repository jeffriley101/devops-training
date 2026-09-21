"""Free, current student snapshot. No writes, review decisions or global standings."""
from datetime import datetime, timezone

from sqlalchemy import select

from .band_director_context import current_roster_period
from .contests import CENTRAL
from .models import CrownAward, PracticeChart, PracticeChartVerification, RewardGrant, Team
from .practice_chart_routes import profile_practice_streak
from .store_inventory import crown_name, PLACEABLE_REWARD_TYPES
from .student_practice_metrics import practice_totals, student_practice_snapshot
from .teams import active_membership, public_team_identity
from .verifiers import accepted_active_verifier_students, band_director_students, select_verifier_student


def recent_achievements(session, *, profile_id: int) -> list[dict]:
    """Bounded persisted earned events; crown bookkeeping and purchases excluded."""
    events = []
    for crown in session.scalars(select(CrownAward).where(CrownAward.profile_id == profile_id)
                                 .order_by(CrownAward.earned_at.desc(), CrownAward.id.desc()).limit(5)):
        events.append({"key": f"crown:{crown.id}", "type": "crown",
                       "label": crown_name(crown.category_key), "icon": "👑", "quantity": 1,
                       "earned_at": crown.earned_at})
    for grant in session.scalars(select(RewardGrant).where(
        RewardGrant.profile_id == profile_id, RewardGrant.reward_type.in_(("trophy", "goat")),
        RewardGrant.amount > 0,
    ).order_by(RewardGrant.created_at.desc(), RewardGrant.id.desc()).limit(5)):
        definition = PLACEABLE_REWARD_TYPES[grant.reward_type]
        events.append({"key": f"reward:{grant.id}", "type": grant.reward_type,
                       "label": definition["name"], "icon": definition["emoji"],
                       "quantity": grant.amount, "earned_at": grant.created_at})
    def order(event):
        stamp = event["earned_at"]
        return (stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp, event["key"])
    return sorted(events, key=order, reverse=True)[:5]


def verifier_dashboard_snapshot(session, *, verifier_id: int, connection_id=None, today=None) -> dict:
    today = today or datetime.now(CENTRAL).date()
    roster = accepted_active_verifier_students(session, verifier_id=verifier_id)
    student = select_verifier_student(roster, connection_id)
    # Internal profile IDs never become request parameters or template payloads.
    choices = [{k: v for k, v in row.items() if k != "profile_id"} for row in roster]
    result = {"connections": choices, "student": None,
              "has_director_students": bool(band_director_students(session, verifier_id=verifier_id))}
    if student is None:
        return result
    profile_id = student["profile_id"]
    charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == profile_id)).all()
    approved = set(session.scalars(select(PracticeChartVerification.practice_chart_id).join(
        PracticeChart, PracticeChart.id == PracticeChartVerification.practice_chart_id,
    ).where(PracticeChart.profile_id == profile_id, PracticeChartVerification.status == "approved")))
    metrics = student_practice_snapshot(charts, approved, today=today)
    metrics.pop("rating_week_values")
    season, _ = current_roster_period(session, today=today)
    season_data = team_data = None
    if season is not None:
        season_charts = [chart for chart in charts if chart.practice_date >= season.starts_on
                         and (season.ends_on is None or chart.practice_date <= season.ends_on)]
        season_totals = practice_totals(season_charts, approved)
        season_data = {"name": season.name, "starts_on": season.starts_on, "ends_on": season.ends_on,
                       "minutes": season_totals["total"], "charts": season_totals["charts"],
                       "days": season_totals["days"], "verified": season_totals["verified"],
                       "pristine": season_totals["pristine"]}
        membership = active_membership(session, profile_id=profile_id, season_id=season.id)
        team = session.get(Team, membership.team_id) if membership else None
        if team is not None and team.season_id == season.id:
            name, _ = public_team_identity(team)
            team_data = {"name": name}
    result["student"] = {k: v for k, v in student.items() if k != "profile_id"}
    result["student"].update(metrics, season=season_data, team=team_data,
                             practice_streak=profile_practice_streak(session, profile_id=profile_id, today=today),
                             achievements=recent_achievements(session, profile_id=profile_id))
    return result


def private_student_metrics(session, *, profile_id, today=None):
    """Shared private calculations; caller must authorize the subject first."""
    today = today or datetime.now(CENTRAL).date()
    charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == profile_id)).all()
    approved = set(session.scalars(select(PracticeChartVerification.practice_chart_id).join(
        PracticeChart, PracticeChart.id == PracticeChartVerification.practice_chart_id,
    ).where(PracticeChart.profile_id == profile_id, PracticeChartVerification.status == "approved")))
    metrics = student_practice_snapshot(charts, approved, today=today)
    metrics.pop("rating_week_values")
    season, _ = current_roster_period(session, today=today)
    season_data = team_data = None
    if season is not None:
        season_charts = [chart for chart in charts if chart.practice_date >= season.starts_on
                         and (season.ends_on is None or chart.practice_date <= season.ends_on)]
        season_totals = practice_totals(season_charts, approved)
        season_data = {"name": season.name, "starts_on": season.starts_on, "ends_on": season.ends_on,
                       "minutes": season_totals["total"], "charts": season_totals["charts"],
                       "days": season_totals["days"], "verified": season_totals["verified"],
                       "pristine": season_totals["pristine"]}
        membership = active_membership(session, profile_id=profile_id, season_id=season.id)
        team = session.get(Team, membership.team_id) if membership else None
        if team is not None and team.season_id == season.id:
            name, _ = public_team_identity(team)
            team_data = {"name": name}
    return dict(metrics, season=season_data, team=team_data,
                practice_streak=profile_practice_streak(session, profile_id=profile_id, today=today),
                achievements=recent_achievements(session, profile_id=profile_id))
