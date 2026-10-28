"""Private student dashboard; no adult, roster, or Classroom reporting authority."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from .account_routes import current_profile
from .age_models import AccountPrivacy
from .age_privacy import eligible
from .db import SessionLocal
from .feature_access import can_use_feature
from .models import PracticeChart, PracticeChartVerification
from .practice_chart_routes import profile_practice_streak
from .practice_charts import PRACTICE_CATEGORIES
from .practice_duration import chart_seconds
from .student_practice_metrics import (
    CENTRAL, WEEK, practice_insights, practice_totals, student_practice_snapshot, week_start,
)

router = APIRouter(prefix='/personal-development', tags=['personal-development'])
PRIVATE_HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
CHART_PAGE_SIZE = 25


def age_allowed(session, profile_id):
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    return bool(rule and rule.age_band in {'13to17', 'adult'} and eligible(session, profile_id))


def access_allowed(session, profile_id):
    # Reuse the narrow personal/Classroom benefit. Never grant an adult rating
    # permission or infer enrollment from a director invitation.
    return age_allowed(session, profile_id) and can_use_feature(session, profile_id, 'practice_insights')


def student(request, session):
    profile = current_profile(request, session)
    if profile is None:
        raise HTTPException(401, 'Student sign-in is required.', headers=PRIVATE_HEADERS)
    if not age_allowed(session, profile.id):
        raise HTTPException(403, {'code': 'age_restricted',
            'message': 'Personal Development Dashboard is available to eligible students aged 13+.'},
            headers=PRIVATE_HEADERS)
    return profile


def authorized_student(request, session):
    profile = student(request, session)
    if not access_allowed(session, profile.id):
        raise HTTPException(403, {'code': 'entitlement_required',
            'message': 'Full Access or eligible Classroom enrollment is required.'}, headers=PRIVATE_HEADERS)
    return profile


def recorded_categories(chart):
    # Historical details may contain custom labels. Never infer an activity or
    # allocate a whole chart's time to each selected category.
    details = chart.practice_details if isinstance(chart.practice_details, list) else []
    return [name for name in PRACTICE_CATEGORIES if name in details]


def snapshot(session, profile_id, *, today=None, rating_only=False):
    """Caller authenticates and authorizes this subject before any chart query."""
    today = today or datetime.now(CENTRAL).date()
    charts = session.scalars(select(PracticeChart).where(
        PracticeChart.profile_id == profile_id).order_by(PracticeChart.practice_date, PracticeChart.id)).all()
    approved = set(session.scalars(select(PracticeChartVerification.practice_chart_id).join(
        PracticeChart, PracticeChart.id == PracticeChartVerification.practice_chart_id).where(
            PracticeChart.profile_id == profile_id, PracticeChartVerification.status == 'approved')))
    # The same personal snapshot used by the existing dashboards. Keep all
    # personal charts, including unshared/opted-out charts. Project no trends.
    metrics = student_practice_snapshot(charts, approved, today=today)
    current = week_start(today)
    result = {'rating': metrics['rating'], 'rating_week': (current - WEEK).isoformat(),
              'rating_week_end': (current - timedelta(days=1)).isoformat()}
    if rating_only:
        return result
    grouped = defaultdict(list)
    by_week = defaultdict(list)
    category_counts = {name: {'name': name, 'weekly_charts': 0, 'lifetime_charts': 0}
                       for name in PRACTICE_CATEGORIES}
    categorized = 0
    for chart in charts:
        grouped[chart.practice_date].append(chart)
        by_week[week_start(chart.practice_date)].append(chart)
        categories = recorded_categories(chart)
        categorized += bool(categories)
        for name in categories:
            category_counts[name]['lifetime_charts'] += 1
            category_counts[name]['weekly_charts'] += current <= chart.practice_date < current + WEEK
    daily = {day: practice_totals(rows, approved)['total_seconds'] for day, rows in grouped.items()}
    earliest = min([current, *by_week])
    history = []
    # Include empty calendar weeks, so an absence of saved practice stays visible.
    for offset in range((current - earliest).days // 7 + 1):
        start = current - WEEK * offset
        history.append({'week_start': start.isoformat(),
                        'week_end': (start + timedelta(days=6)).isoformat(),
                        **practice_totals(by_week[start], approved),
                        'daily': [{'date': (start + timedelta(days=i)).isoformat(),
                                   'seconds': daily.get(start + timedelta(days=i), 0)} for i in range(7)]})
    lifetime = []
    cumulative = 0
    # Sparse calendar points retain the full lifetime and zero-practice gaps.
    # Add the day before each increase so the line doesn't imply practice
    # throughout a long gap. No fixed window or sampled totals are substituted.
    for day in sorted(daily):
        if not lifetime or (day - timedelta(days=1)).isoformat() > lifetime[-1]['date']:
            lifetime.append({'date': (day - timedelta(days=1)).isoformat(), 'seconds': cumulative})
        cumulative += daily[day]
        lifetime.append({'date': day.isoformat(), 'seconds': cumulative})
    if lifetime and lifetime[-1]['date'] < today.isoformat():
        lifetime.append({'date': today.isoformat(), 'seconds': cumulative})
    return {**result, 'as_of': today.isoformat(), 'week_start': current.isoformat(),
            'week_end': (current + timedelta(days=6)).isoformat(),
            'lifetime_seconds': metrics['lifetime']['total_seconds'],
            'weekly_seconds': metrics['weekly']['total_seconds'],
            'lifetime': lifetime,
            'weekly': [{'date': (current + timedelta(days=i)).isoformat(),
                        'seconds': daily.get(current + timedelta(days=i), 0)} for i in range(7)],
            'totals': {period: metrics[period] for period in ('weekly', 'lifetime')},
            'current_verified_streak': profile_practice_streak(session, profile_id, today=today),
            'first_practice': min((day.isoformat() for day, seconds in daily.items() if seconds > 0), default=None),
            'last_practice': max((day.isoformat() for day, seconds in daily.items() if seconds > 0), default=None),
            'history': history,
            'categories': list(category_counts.values()),
            'categorized_charts': categorized,
            'insights': practice_insights(charts, approved, today=today)}


def response(request, *, rating_only=False):
    with SessionLocal() as session:
        profile = authorized_student(request, session)
        return JSONResponse(snapshot(session, profile.id, rating_only=rating_only), headers=PRIVATE_HEADERS)


@router.get('/data')
def dashboard_data(request: Request):
    return response(request)


@router.get('/rating')
def book_rating(request: Request):
    return response(request, rating_only=True)


@router.get('/charts')
def chart_history(request: Request, page: int = Query(1, ge=1, le=1000000)):
    """Bounded submission history, always scoped to the authenticated student."""
    with SessionLocal() as session:
        profile = authorized_student(request, session)
        scope = PracticeChart.profile_id == profile.id
        count = session.scalar(select(func.count()).select_from(PracticeChart).where(scope))
        charts = session.scalars(select(PracticeChart).where(scope).order_by(
            PracticeChart.created_at.desc(), PracticeChart.id.desc()).offset(
                (page - 1) * CHART_PAGE_SIZE).limit(CHART_PAGE_SIZE)).all()
        approved = set(session.scalars(select(PracticeChartVerification.practice_chart_id).join(
            PracticeChart, PracticeChart.id == PracticeChartVerification.practice_chart_id).where(
                scope, PracticeChart.id.in_([chart.id for chart in charts]),
                PracticeChartVerification.status == 'approved')))
        rows = []
        for chart in charts:
            submitted = chart.created_at
            if submitted.tzinfo is None:
                submitted = submitted.replace(tzinfo=timezone.utc)
            rows.append({'practice_date': chart.practice_date.isoformat(),
                         'submitted_at': submitted.isoformat(), 'seconds': chart_seconds(chart),
                         'kind': ('Pristine' if chart.source == 'pristine' else
                                  'Verified' if chart.id in approved else 'Reported'),
                         'categories': recorded_categories(chart)})
        return JSONResponse({'charts': rows, 'page': page, 'page_size': CHART_PAGE_SIZE,
                             'total': count}, headers=PRIVATE_HEADERS)
