"""Read-only weekly date/credit projection; no rewards or publication writes."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .practice_duration import chart_seconds

CENTRAL = ZoneInfo("America/Chicago")


def utc_instant(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Weekly time must be timezone-aware.")
    return now.astimezone(timezone.utc)


def eligible_chart(chart):
    return chart.source in {"p-book", "pristine"} and chart_seconds(chart) > 0


def weekly_credit(charts, *, week_start: date, now=None, instruments=None, cutoff=None):
    """All saved credit, including intentional Pristine + separate BOOK credit.

    A frozen caller supplies its recorded source cutoff. No skill completion or
    verification is inferred. Logging uses server creation day across instruments.
    """
    instant = utc_instant(now)
    limit = min(instant, utc_instant(cutoff)) if cutoff else instant
    today = instant.astimezone(CENTRAL).date()
    end = week_start + timedelta(days=7)
    seconds, dates, logging = 0, set(), set()
    for chart in charts:
        # DB timestamps may be returned naive by SQLite; they were stored in UTC.
        submitted = chart.created_at
        submitted = submitted.replace(tzinfo=timezone.utc) if submitted.tzinfo is None else submitted
        if submitted > limit or not eligible_chart(chart) or chart.practice_date > today:
            continue
        if instruments is not None and chart.instrument not in instruments:
            continue
        if week_start <= chart.practice_date < end:
            seconds += chart_seconds(chart)
            dates.add(chart.practice_date)
        submitted_day = submitted.astimezone(CENTRAL).date()
        if week_start <= submitted_day < end:
            logging.add(submitted_day)
    return {"practice_seconds": seconds, "practice_days": len(dates),
            "logging_days": len(logging)}


def first_seven_date_milestone(charts, *, now=None):
    """Seven consecutive 5-minute dates, credited only by same-day submissions.

    This milestone's timeliness rule does not change ordinary practice credit.
    SQLite's naive stored timestamps represent UTC, as in weekly_credit().
    """
    instant = utc_instant(now)
    today = instant.astimezone(CENTRAL).date()
    daily = {}
    for chart in charts:
        submitted = chart.created_at
        submitted = submitted.replace(tzinfo=timezone.utc) if submitted.tzinfo is None else submitted
        if (eligible_chart(chart) and chart.practice_date <= today and submitted <= instant
                and submitted.astimezone(CENTRAL).date() == chart.practice_date):
            daily[chart.practice_date] = daily.get(chart.practice_date, 0) + chart_seconds(chart)
    run, previous = 0, None
    for day in sorted(day for day, seconds in daily.items() if seconds >= 300):
        run = run + 1 if previous == day - timedelta(days=1) else 1
        if run == 7:
            return day
        previous = day
    return None
