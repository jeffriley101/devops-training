"""V3 practice projections from individually shared charts, never a full roster.

Read within the sharing transaction. No personal rating, private-chart query,
comprehensive reporting grant, or persisted snapshot is created here.
"""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from .age_privacy import utc
from .classroom_chart_sharing import _shared_chart_records
from . import classroom_reporting
from .models import PracticeChartVerification
from .practice_charts import PRACTICE_CATEGORIES
from .practice_duration import chart_seconds
from .student_practice_metrics import CENTRAL, practice_totals, week_start


def _categories(charts):
    # One chart can contribute to many categories; repeats within it count once.
    counts = dict.fromkeys(PRACTICE_CATEGORIES, 0)
    for chart in charts:
        for category in set(chart.practice_details or ()):
            if category in counts:
                counts[category] += 1
    return list(counts.values())


def _timestamp(value):
    return utc(value).isoformat() if value is not None else None


def dashboard(session, *, actor, program_id, class_id):
    records = _shared_chart_records(session, actor=actor, program_id=program_id, class_id=class_id)
    # Only verification status for already-authorized chart IDs. Never verifier
    # identities, responses, contact details, or other students' verifications.
    ids = [chart.id for _, _, chart in records]
    approved = set()
    for offset in range(0, len(ids), 500):
        approved.update(session.scalars(select(PracticeChartVerification.practice_chart_id).where(
            PracticeChartVerification.practice_chart_id.in_(ids[offset:offset + 500]),
            PracticeChartVerification.status == "approved")))
    now = classroom_reporting._now()
    current = week_start(now.astimezone(CENTRAL).date())
    weeks = {current - timedelta(weeks=n) for n in range(8)}
    grouped = defaultdict(list)
    names = {}
    for profile_id, name, chart in records:
        grouped[profile_id].append(chart)
        names[profile_id] = name
    students = []
    for profile_id, charts in sorted(grouped.items(), key=lambda item: (names[item[0]].casefold(), item[0])):
        by_week = defaultdict(list)
        by_day = defaultdict(int)
        submissions = defaultdict(int)
        for chart in charts:
            start = week_start(chart.practice_date)
            weeks.add(start)
            by_week[start].append(chart)
            by_day[chart.practice_date] += chart_seconds(chart)
            if chart.source == "p-book" and chart.created_at is not None:
                submitted_week = week_start(utc(chart.created_at).astimezone(CENTRAL).date())
                weeks.add(submitted_week)
                submissions[submitted_week] += 1
        weekly = {}
        for start in by_week.keys() | submissions.keys():
            selected = by_week[start]
            weekly[start.isoformat()] = {
                **practice_totals(selected, approved), "categories": _categories(selected),
                "submissions": submissions[start],
                "daily_seconds": [by_day.get(start + timedelta(days=day), 0) for day in range(7)],
            }
        cumulative = 0
        history = []
        for day, seconds in sorted(by_day.items()):
            cumulative += seconds
            history.append({"date": day.isoformat(), "seconds": seconds, "cumulative_seconds": cumulative})
        serialized = [{
            "date": chart.practice_date.isoformat(), "seconds": chart_seconds(chart),
            "submitted_at": _timestamp(chart.created_at), "source": chart.source,
            "verification": "Pristine" if chart.source == "pristine" else (
                "Verified" if chart.id in approved else "Unverified"),
            "instrument": chart.instrument, "categories": chart.practice_details or [],
            "note": chart.note or "",
        } for chart in sorted(charts, key=lambda c: (_timestamp(c.created_at) or "", c.id), reverse=True)]
        submitted = [c["submitted_at"] for c in serialized if c["source"] == "p-book" and c["submitted_at"]]
        students.append({
            # A response-local row key, never a student account lookup capability.
            "key": str(len(students)), "name": names[profile_id],
            "instruments": sorted({chart.instrument for chart in charts}),
            "lifetime": practice_totals(charts, approved), "weekly": weekly,
            "categories": _categories(charts), "last_submission": max(submitted, default=None),
            "history": history, "charts": serialized,
        })
    return {"scope": "explicitly_shared_charts", "as_of": now.isoformat(),
            "current_week": current.isoformat(), "weeks": [week.isoformat() for week in sorted(weeks, reverse=True)],
            "categories": list(PRACTICE_CATEGORIES), "students": students}
