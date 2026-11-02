"""Leadership's 41-field individual report, using current educational consent.

The boundary-only APIs stay boundary-only. This reader accepts no decision token,
queries no free-text notes or account preferences, and publishes no cohort data.
All source reads occur under the accepted Program-first authorization transaction.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from . import classroom as s1, classroom_progress_authorization as progress
from .age_privacy import utc
from .models import PracticeChart, PracticeChartVerification
from .practice_charts import PRACTICE_CATEGORIES
from .practice_duration import chart_seconds
from .student_practice_metrics import CENTRAL, WEEK, practice_totals, week_start
from .weekly import weekly_credit
from .weekly_models import WeeklyInstrumentPlan, WeeklyPlan
from .instruments import canonical_instrument_key

CONTRACT_VERSION = "classroom-reporting-41-v1"
# Leadership reconfirmed the September 11 square-root/five-day equation in the
# implementation authorization. The shared helper is covered by mathematical
# regression tests; Team Practice Rating is a separate, unchanged calculation.
RATING_FORMULA_VERIFIED = True

FIELD_LABELS = (
    ("weekly_practice_minutes", "Weekly practice minutes"),
    ("lifetime_practice_minutes", "Lifetime practice minutes"),
    ("weekly_practice_days", "Weekly practice days"),
    ("weekly_practice_charts", "Weekly Practice Charts"),
    ("lifetime_practice_charts", "Lifetime Practice Charts"),
    ("weekly_verified_minutes", "Weekly Verified minutes"),
    ("weekly_pristine_minutes", "Weekly Pristine minutes"),
    ("lifetime_verified_minutes", "Lifetime Verified minutes"),
    ("lifetime_pristine_minutes", "Lifetime Pristine minutes"),
    ("last_p_chart_submission", "Last P-Chart submission"),
    ("lifetime_practice_graph", "Lifetime practice graph"),
    ("weekly_practice_graph", "Weekly practice graph"),
) + tuple(("category_" + name.lower().replace(" ", "_").replace("-", "_"), name)
          for name in PRACTICE_CATEGORIES) + (
    ("student_practice_rating", "Student Practice Rating"),
    ("rating_trend", "Rating trend"),
    ("current_practice_streak", "Current practice streak"),
    ("weekly_focus", "Weekly Focus"),
    ("homework_participation", "Homework participation"),
    ("homework_completion", "Homework completion"),
)
FIELD_KEYS = tuple(key for key, _ in FIELD_LABELS)
assert len(FIELD_KEYS) == len(set(FIELD_KEYS)) == 41


def _midnight(day):
    return datetime.combine(day, time.min, CENTRAL).astimezone(timezone.utc)


def _first_day(instant):
    day = instant.astimezone(CENTRAL).date()
    return day if _midnight(day) == instant else day + timedelta(days=1)


def _days(start, end):
    while start < end:
        yield start
        start += timedelta(days=1)


def _charts(session, profile_id, decision):
    # Entire Central occurrence days, as in the accepted S3 history checker.
    # SQL excludes unauthorized records before selecting any measurement.
    first = _first_day(decision.starts_at)
    end = decision.ends_at.astimezone(CENTRAL).date()
    rows = session.execute(select(
        PracticeChart.id, PracticeChart.practice_date, PracticeChart.created_at,
        PracticeChart.minutes, PracticeChart.detected_playing_seconds,
        PracticeChart.source, PracticeChart.instrument, PracticeChart.practice_details,
    ).where(
        PracticeChart.profile_id == profile_id,
        PracticeChart.practice_date >= first, PracticeChart.practice_date < end,
        PracticeChart.created_at >= decision.starts_at,
        PracticeChart.created_at <= decision.ends_at,
    ).order_by(PracticeChart.practice_date, PracticeChart.id)).all()
    # Defend the invariant independently of dialect date/timestamp coercion.
    return [row for row in rows if decision.starts_at <= _midnight(row.practice_date)
            and _midnight(row.practice_date + timedelta(days=1)) <= decision.ends_at
            and decision.starts_at <= utc(row.created_at) <= decision.ends_at]


def _verified(session, charts, now):
    approved = set()
    ids = [chart.id for chart in charts]
    for offset in range(0, len(ids), 500):
        # No verifier identity/contact/response content is read.
        rows = session.execute(select(PracticeChartVerification.practice_chart_id,
            PracticeChartVerification.responded_at).where(
                PracticeChartVerification.practice_chart_id.in_(ids[offset:offset + 500]),
                PracticeChartVerification.status == "approved")).all()
        approved.update(row.practice_chart_id for row in rows
                        if row.responded_at is None or utc(row.responded_at) <= now)
    return approved


def _homework(session, profile_id, selected_week, decision, charts, covered):
    result = {"weekly_focus": None, "homework_participation": None, "homework_completion": None}
    if not covered:
        return result
    plan = session.execute(select(WeeklyPlan.id, WeeklyPlan.week_start, WeeklyPlan.focus,
        WeeklyPlan.issued_at, WeeklyPlan.homework_minutes_target).where(
            WeeklyPlan.profile_id == profile_id, WeeklyPlan.week_start == selected_week,
            WeeklyPlan.issued_at >= decision.starts_at,
            WeeklyPlan.issued_at <= decision.ends_at)).one_or_none()
    if plan is None:
        return result  # An absent plan is unavailable, not failed Homework.
    result["weekly_focus"] = plan.focus
    assignments = session.execute(select(WeeklyInstrumentPlan.instrument,
        WeeklyInstrumentPlan.started_at).where(WeeklyInstrumentPlan.plan_id == plan.id)
        .order_by(WeeklyInstrumentPlan.slot_no)).all()
    if any(not (max(decision.starts_at, utc(plan.issued_at)) <= utc(row.started_at)
                    <= decision.ends_at) for row in assignments):
        return result
    result["homework_participation"] = bool(assignments)
    # No assignment means no supported completion measurement.
    if not assignments:
        return result
    end_day = selected_week + WEEK
    end = _midnight(end_day)
    qualified = []
    for assignment in assignments:
        started = utc(assignment.started_at)
        try:
            instrument = canonical_instrument_key(assignment.instrument)
        except ValueError:
            return result
        matching = []
        for chart in charts:
            try:
                matches = canonical_instrument_key(chart.instrument) == instrument
            except ValueError:
                matches = False
            if (matches and max(selected_week, started.astimezone(CENTRAL).date())
                    <= chart.practice_date < end_day and started < utc(chart.created_at) < end):
                matching.append(chart)
        seconds = weekly_credit(matching, week_start=selected_week,
            now=decision.ends_at)["practice_seconds"]
        qualified.append(seconds >= plan.homework_minutes_target * 60)
    # Equivalent to weekly_tasks.homework_progress; no unbounded helper query.
    if any(qualified):
        result["homework_completion"] = True
    elif end <= decision.ends_at:
        result["homework_completion"] = False
    # For an open week, an unreportable current-day chart could already meet
    # the target. Lack of visible qualification is not evidence of failure.
    return result


def unavailable(authorized=False):
    return {"source": "separate_reporting_authorization", "authorization_available": authorized,
        "field_contract": CONTRACT_VERSION, "field_labels": FIELD_LABELS, "values": None,
        "message": "Statistics are unavailable without current reporting authorization. "
                   "Open an individual student to view eligible reporting."}


@s1._mutation
def report(session, *, actor, program_id, class_id, profile_id, selected_week=None, fields=FIELD_KEYS):
    """Only the approved fixed projection; no caller-controlled field expansion."""
    if fields != FIELD_KEYS or type(fields) is not tuple:
        raise s1.ClassroomDenied("Classroom reporting unavailable.")
    if selected_week is not None and (type(selected_week) is not date or selected_week.weekday() != 0):
        raise s1.ClassroomDenied("Classroom reporting unavailable.")
    decision = progress.authorize_boundary(session, actor=actor, program_id=program_id,
        class_id=class_id, profile_id=profile_id)
    today = decision.ends_at.astimezone(CENTRAL).date()
    current = week_start(today)
    selected_week = selected_week or current
    if selected_week > current:
        raise s1.ClassroomDenied("Classroom reporting unavailable.")
    first = _first_day(decision.starts_at)
    charts = _charts(session, profile_id, decision)
    approved = _verified(session, charts, decision.ends_at)
    values = dict.fromkeys(FIELD_KEYS)
    lifetime = practice_totals(charts, approved)
    # No complete occurrence day has become eligible yet: don't fabricate zero.
    if first < today:
        values.update(lifetime_practice_minutes=lifetime["total"],
            lifetime_practice_charts=lifetime["charts"],
            lifetime_verified_minutes=lifetime["verified"],
            lifetime_pristine_minutes=lifetime["pristine"])
        running = 0
        history = []
        by_day = {}
        for chart in charts:
            by_day[chart.practice_date] = by_day.get(chart.practice_date, 0) + chart_seconds(chart)
        for day in _days(first, today):
            running += by_day.get(day, 0)
            history.append({"date": day.isoformat(), "minutes": running / 60})
        values["lifetime_practice_graph"] = history
    # A partially authorized week is not a complete weekly measurement. Current
    # week readings cover completed authorized occurrence days only, explicitly
    # labelled in the UI; today's incomplete day is never projected.
    covered = _midnight(selected_week) >= decision.starts_at and selected_week < today
    selected = [c for c in charts if selected_week <= c.practice_date < selected_week + WEEK]
    if covered:
        weekly = practice_totals(selected, approved)
        values.update(weekly_practice_minutes=weekly["total"], weekly_practice_days=weekly["days"],
            weekly_practice_charts=weekly["charts"], weekly_verified_minutes=weekly["verified"],
            weekly_pristine_minutes=weekly["pristine"])
        categories = {item for c in selected for item in (c.practice_details or ()) if isinstance(item, str)}
        for name in PRACTICE_CATEGORIES:
            key = "category_" + name.lower().replace(" ", "_").replace("-", "_")
            values[key] = name in categories
        values["weekly_practice_graph"] = [{"date": day.isoformat(),
            "minutes": sum(chart_seconds(c) for c in selected if c.practice_date == day) / 60
                if day < today else None} for day in _days(selected_week, selected_week + WEEK)]
    submissions = [utc(c.created_at) for c in charts if c.source == "p-book"]
    if submissions:
        values["last_p_chart_submission"] = max(submissions).astimezone(CENTRAL).isoformat()
    rating_week = current - WEEK
    if RATING_FORMULA_VERIFIED:
        from .student_practice_metrics import student_practice_rating, trend
        ratings = []
        for offset in range(5):
            start = rating_week - WEEK * offset
            if _midnight(start) < decision.starts_at or _midnight(start + WEEK) > decision.ends_at:
                ratings.append(None)
                continue
            totals = practice_totals([c for c in charts if start <= c.practice_date < start + WEEK], approved)
            ratings.append(student_practice_rating(totals["total"], totals["days"],
                totals["verified"] > 0, totals["pristine"] > 0))
        values["student_practice_rating"] = ratings[0]
        if all(value is not None for value in ratings):
            values["rating_trend"] = trend(ratings[0] - sum(ratings[1:]) / 4)
    # The accepted history policy excludes the incomplete current occurrence
    # day. An exact current streak cannot be inferred from yesterday alone.
    values["current_practice_streak"] = None
    values.update(_homework(session, profile_id, selected_week, decision, charts, covered))
    s1._actor(session, actor)
    return {"source": "separate_reporting_authorization", "authorization_available": True,
        "field_contract": CONTRACT_VERSION, "field_labels": FIELD_LABELS,
        "message": "Individual authorized history only. Practice uses complete eligible occurrence days; "
            "today's incomplete day and preauthorization history are excluded. "
            "Current practice streak is unavailable because the current day is outside that boundary. "
            + ("" if RATING_FORMULA_VERIFIED else
               "Rating and trend are unavailable pending verification of the approved scoring equation."),
        "values": {"fields": values, "selected_week": selected_week.isoformat(),
            "rating_week": rating_week.isoformat(), "as_of": decision.ends_at.isoformat()}}
