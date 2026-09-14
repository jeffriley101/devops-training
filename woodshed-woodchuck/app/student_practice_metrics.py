"""Read-only practice snapshots for callers that have already authorized a student.

These are practice metrics, not contest eligibility or XP chart counts. The
four-week Insights summary reuses the same totals without changing dashboard
snapshot/rating semantics or introducing advanced analytics.
"""
from datetime import date, datetime, time, timedelta

from .contests import CENTRAL, central_week_boundaries

WEEK = timedelta(days=7)


def practice_insights(charts, approved: set[int], *, today: date) -> dict:
    """Four completed Central calendar weeks, oldest first; persisted minutes only."""
    end = week_start(today)
    weeks = []
    for offset in range(4, 0, -1):
        start = end - WEEK * offset
        totals = practice_totals([chart for chart in charts
                                 if start <= chart.practice_date < start + WEEK], approved)
        weeks.append({"week_start": start.isoformat(),
                      "week_end": (start + WEEK - timedelta(days=1)).isoformat(),
                      "minutes": totals["total"], "days": totals["days"],
                      "verified_minutes": totals["verified"],
                      "pristine_minutes": totals["pristine"]})
    total = sum(week["minutes"] for week in weeks)
    return {"weeks": weeks, "total_minutes": total, "average_weekly_minutes": total / 4}


def week_start(day: date) -> date:
    return central_week_boundaries(datetime.combine(day, time.min, CENTRAL))[0]


def student_practice_rating(minutes: int, days: int, verified: bool, pristine: bool) -> float:
    # Volume supplies 95% of the core; four distinct positive-practice days
    # supply at most 5%. Each category bonus is awarded once, never per minute.
    core = min(max(minutes, 0), 120) / 120 * (95 + 5 * min(max(days, 0), 4) / 4)
    return min(104.0, core + (3 if verified else 0) + (1 if pristine else 0))


def trend(delta: float) -> dict:
    direction = "up" if delta > 3 else "down" if delta < -3 else "steady"
    return {"delta": delta, "direction": direction,
            "arrow": {"up": "↑", "down": "↓", "steady": "→"}[direction],
            "label": {"up": "Increasing", "down": "Decreasing", "steady": "Steady"}[direction]}


def practice_totals(charts, approved: set[int]) -> dict:
    # Include contest opt-outs. Pristine takes precedence even if an anomalous
    # Pristine chart has an approved verification. Count all records, not XP awards.
    positive = [chart for chart in charts if chart.minutes > 0]
    return {
        "total": sum(chart.minutes for chart in positive),
        "verified": sum(chart.minutes for chart in positive
                        if chart.source != "pristine" and chart.id in approved),
        "pristine": sum(chart.minutes for chart in positive if chart.source == "pristine"),
        "charts": len(charts),
        "days": len({chart.practice_date for chart in positive}),
    }


def student_practice_snapshot(charts, approved: set[int], *, today: date,
                              selected_week: date | None = None) -> dict:
    current_week = week_start(today)
    selected_week = selected_week or current_week
    rating_week = selected_week if selected_week < current_week else current_week - WEEK

    def weekly(start):
        return practice_totals([chart for chart in charts
                                if start <= chart.practice_date < start + WEEK], approved)

    values = []
    for offset in range(5):
        totals = weekly(rating_week - WEEK * offset)
        values.append(student_practice_rating(totals["total"], totals["days"],
                                             totals["verified"] > 0, totals["pristine"] > 0))
    return {"weekly": weekly(selected_week), "lifetime": practice_totals(charts, approved),
            "rating": values[0], "trend": trend(values[0] - sum(values[1:]) / 4),
            "rating_week_values": values}
