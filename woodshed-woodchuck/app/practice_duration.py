"""Reported chart duration and independent earning qualification."""
from sqlalchemy import case


def chart_seconds(chart) -> int:
    seconds = getattr(chart, "detected_playing_seconds", None)
    if chart.source == "pristine" and type(seconds) is int and 0 <= seconds <= 86400:
        return seconds
    return max(0, chart.minutes) * 60


def chart_seconds_sql():
    """SQL equivalent of chart_seconds for persisted integer duration columns."""
    from .models import PracticeChart as Chart
    return case(
        ((Chart.source == "pristine") & Chart.detected_playing_seconds.between(0, 86400),
         Chart.detected_playing_seconds),
        else_=case((Chart.minutes > 0, Chart.minutes * 60), else_=0),
    )


def format_seconds(seconds: float) -> str:
    # Only presentation rounds; weekly averages can contain fractional seconds.
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = [f"{value} {unit}{'s' if value != 1 else ''}"
             for value, unit in ((hours, "hour"), (minutes, "minute"), (seconds, "second")) if value]
    return " ".join(parts) or "0 minutes"


def format_minutes(minutes: float) -> str:
    return format_seconds(minutes * 60)


def qualified_practice_clause():
    """Only independently approved BOOK records are earning/contest evidence.

    Reported duration remains available in private logs. A browser's Pristine
    source flag or microphone duration is never an independent verification.
    """
    from sqlalchemy import exists
    from .models import PracticeChart, PracticeChartVerification
    return (PracticeChart.source == "p-book") & exists().where(
        PracticeChartVerification.practice_chart_id == PracticeChart.id,
        PracticeChartVerification.status == "approved",
        PracticeChartVerification.responded_at.is_not(None),
    )
