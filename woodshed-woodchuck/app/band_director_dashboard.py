"""Read-only, roster-scoped dashboard metrics; never contest scores or rewards."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .band_director_context import current_roster_period
from .contests import CENTRAL, central_week_boundaries
from .models import PracticeChart, PracticeChartVerification, Team, TeamMembership
from .teams import public_team_identity
from .verifiers import band_director_students


WEEK = timedelta(days=7)


def week_start(day: date) -> date:
    return central_week_boundaries(datetime.combine(day, time.min, CENTRAL))[0]


def student_practice_rating(minutes: int, days: int, verified: bool, pristine: bool) -> float:
    # Volume supplies 95% of the core; four distinct positive-practice days
    # supply at most 5%. Concentrated practice still earns almost the full core.
    core = min(max(minutes, 0), 120) / 120 * (95 + 5 * min(max(days, 0), 4) / 4)
    return min(104.0, core + (3 if verified else 0) + (1 if pristine else 0))


def trend(delta: float) -> dict:
    direction = "up" if delta > 3 else "down" if delta < -3 else "steady"
    return {"delta": delta, "direction": direction,
            "arrow": {"up": "↑", "down": "↓", "steady": "→"}[direction],
            "label": {"up": "Increasing", "down": "Decreasing", "steady": "Steady"}[direction]}


def dashboard_metrics(session: Session, *, verifier_id: int,
                      selected_week: date | None = None, today: date | None = None) -> dict:
    today = today or datetime.now(CENTRAL).date()
    current_week = week_start(today)
    roster = band_director_students(session, verifier_id=verifier_id)
    ids = [student["profile_id"] for student in roster]
    # Batch the entire authorized roster, not one global standings calculation
    # per student. Neither request parameters nor reviews can expand these IDs.
    charts = session.scalars(select(PracticeChart).where(
        PracticeChart.profile_id.in_(ids),
    ).order_by(PracticeChart.created_at.desc(), PracticeChart.id.desc())).all() if ids else []
    reviews = session.scalars(select(PracticeChartVerification).join(PracticeChart).where(
        PracticeChart.profile_id.in_(ids),
    ).order_by(PracticeChartVerification.id)).all() if ids else []
    approved = {review.practice_chart_id for review in reviews if review.status == "approved"}

    earliest = min([current_week] + [week_start(chart.practice_date) for chart in charts])
    weeks = [current_week - WEEK * offset for offset in range((current_week - earliest).days // 7 + 1)]
    selected_week = selected_week or current_week
    if selected_week not in weeks:
        raise ValueError("Choose an available week.")
    rating_week = selected_week if selected_week < current_week else current_week - WEEK
    by_student = defaultdict(list)
    for chart in charts:
        by_student[chart.profile_id].append(chart)

    # Team is the current-season active membership, independent of the selected
    # practice week. Historical contest snapshots/rankings are not recomputed.
    season, _ = current_roster_period(session, today=today)
    teams = {}
    if season is not None and ids:
        for profile_id, team in session.execute(select(TeamMembership.profile_id, Team).join(
            Team, Team.id == TeamMembership.team_id,
        ).where(TeamMembership.profile_id.in_(ids), TeamMembership.season_id == season.id,
                TeamMembership.ended_at.is_(None), Team.season_id == season.id)):
            name, emblem = public_team_identity(team)
            teams.setdefault(profile_id, {"name": name, "emblem": emblem})

    students = []
    program_week_sums = [0.0] * 5
    for student in roster:
        profile_id = student["profile_id"]
        history = by_student[profile_id]

        def totals(items):
            # Match practice_totals_payload: positive persisted practice minutes,
            # including contest opt-outs. Approval is practice verification, not
            # contest deadline eligibility. Pristine takes precedence, so an
            # anomalous approved Pristine chart can never count in both columns.
            positive = [chart for chart in items if chart.minutes > 0]
            return {
                "total": sum(chart.minutes for chart in positive),
                "verified": sum(chart.minutes for chart in positive
                                if chart.source != "pristine" and chart.id in approved),
                "pristine": sum(chart.minutes for chart in positive if chart.source == "pristine"),
                "charts": len(items),
                "days": len({chart.practice_date for chart in positive}),
            }

        weekly_charts = [chart for chart in history
                         if selected_week <= chart.practice_date < selected_week + WEEK]

        def rating(start):
            values = totals([chart for chart in history if start <= chart.practice_date < start + WEEK])
            return student_practice_rating(values["total"], values["days"],
                                           values["verified"] > 0, values["pristine"] > 0)

        values = [rating(rating_week - WEEK * offset) for offset in range(5)]
        value = values[0]
        baseline = sum(values[1:]) / 4
        for index, score in enumerate(values):
            program_week_sums[index] += score
        students.append({"display_name": student["display_name"],
                         "rating": value, "trend": trend(value - baseline),
                         "weekly": totals(weekly_charts), "lifetime": totals(history),
                         "team": teams.get(profile_id)})
    count = len(students)
    # All five weeks use the currently authorized cohort, including zeros.
    # Averaging student deltas is equivalent only with this fixed cohort.
    # Historical director-roster snapshots do not exist.
    program_weeks = [score / count if count else 0 for score in program_week_sums]
    program_baseline = sum(program_weeks[1:]) / 4
    return {
        "students": students,
        "program_rating": program_weeks[0],
        "program_trend": trend(program_weeks[0] - program_baseline),
        "weeks": weeks, "selected_week": selected_week,
        "week_end": selected_week + timedelta(days=6),
        "previous_week": selected_week - WEEK if selected_week > earliest else None,
        "next_week": selected_week + WEEK if selected_week < current_week else None,
    }
