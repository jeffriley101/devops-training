"""Read-only, roster-scoped dashboard metrics; never contest scores or rewards."""

from collections import defaultdict
import csv
from io import StringIO
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .band_director_context import current_roster_period
from .contests import CENTRAL
from .models import PracticeChart, PracticeChartVerification, Team, TeamMembership
from .teams import public_team_identity
from .verifiers import band_director_students
from .student_practice_metrics import (
    WEEK, week_start, student_practice_rating, trend, student_practice_snapshot,
)


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

        snapshot = student_practice_snapshot(history, approved, today=today, selected_week=selected_week)
        for index, score in enumerate(snapshot.pop("rating_week_values")):
            program_week_sums[index] += score
        students.append({"display_name": student["display_name"],
                         "instrument": student["instrument"], "level": student["level"], **snapshot,
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


CSV_COLUMNS = (
    "Student", "Instrument", "Level", "Team", "Week Start", "Week End",
    "Practice Minutes", "Practice Days", "Practice Rating", "Trend",
    "Verified Minutes", "Pristine Minutes",
    "Practice Seconds", "Verified Seconds", "Pristine Seconds",
    "Career Practice Seconds", "Career Verified Seconds", "Career Pristine Seconds",
)


def dashboard_csv(metrics: dict) -> str:
    """Export only public snapshot fields; no fresh queries or calculations."""
    def text_cell(value):
        value = str(value)
        # CSV quoting handles delimiters, but not spreadsheet formula execution.
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value

    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for student in metrics["students"]:
        writer.writerow([
            *map(text_cell, (student["display_name"], student["instrument"], student["level"],
                            student["team"]["name"] if student["team"] else "No team")),
            metrics["selected_week"].isoformat(), metrics["week_end"].isoformat(),
            format(student["weekly"]["total"], ".15g"), student["weekly"]["days"], student["rating"],
            student["trend"]["label"], format(student["weekly"]["verified"], ".15g"), format(student["weekly"]["pristine"], ".15g"),
            *[student[period][f"{kind}_seconds"] for period in ("weekly", "lifetime")
              for kind in ("total", "verified", "pristine")],
        ])
    return output.getvalue()
