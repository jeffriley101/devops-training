"""Read-only practice summaries for the authorized Band Director roster."""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PracticeChart, PracticeChartVerification
from .practice_chart_routes import CENTRAL, practice_totals_payload
from .verifiers import band_director_students
from .band_director_context import current_roster_period, student_contest_context


RECENT_CHART_LIMIT = 5


def band_director_practice_students(
    session: Session, *, verifier_id: int, today: date | None = None
) -> list[dict[str, object]]:
    today = today or datetime.now(CENTRAL).date()
    students = []
    season, week = current_roster_period(session, today=today)
    for student in band_director_students(session, verifier_id=verifier_id):
        profile_id = student.pop("profile_id")
        totals = practice_totals_payload(session, profile_id, today=today)
        charts = session.scalars(
            select(PracticeChart)
            .where(PracticeChart.profile_id == profile_id)
            .order_by(PracticeChart.created_at.desc(), PracticeChart.id.desc())
            .limit(RECENT_CHART_LIMIT)
        ).all()
        reviews = session.scalars(
            select(PracticeChartVerification)
            .where(PracticeChartVerification.practice_chart_id.in_([chart.id for chart in charts]))
            .order_by(PracticeChartVerification.requested_at, PracticeChartVerification.id)
        ).all() if charts else []
        recent_charts = []
        for chart in charts:
            submitted = chart.created_at
            if submitted.tzinfo is None:
                submitted = submitted.replace(tzinfo=timezone.utc)
            recent_charts.append({
                "practice_date": chart.practice_date.isoformat(),
                "minutes": chart.minutes,
                "submitted_at": submitted.isoformat(),
                "submitted_display": submitted.astimezone(CENTRAL).strftime("%b %d, %Y %I:%M %p %Z"),
                "unreviewed_label": "Pristine" if chart.source == "pristine" else "Open — no verification requested",
                "verifications": [
                    {
                        "status": review.status,
                        "review_id": review.id if (
                            review.verifier_id == verifier_id
                            and review.status == "pending"
                            and chart.source == "p-book"
                        ) else None,
                        # Another adult's identity and response notes remain private.
                        "response_note": review.response_note if review.verifier_id == verifier_id else None,
                    }
                    for review in reviews if review.practice_chart_id == chart.id
                ],
            })
        students.append({
            **student,
            "week_start": totals["week_start"],
            "week_end": totals["week_end"],
            "this_week_minutes": totals["this_week_minutes"],
            "recent_charts": recent_charts,
            **student_contest_context(session, profile_id=profile_id, season=season, week=week),
        })
    return students
