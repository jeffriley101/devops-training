"""Historical publication must not requalify or reward saved contest results."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app import contests
from app.age_models import AccountPrivacy
from app.age_privacy import declare_age, filter_hall_result_rows
from app.db import Base
from app.models import (
    Contest, ContestResult, ContestWeek, CrownProgress, PracticeChart,
    RewardGrant, Season, WoodchuckProfile, WoodchuckState,
)
from app.xp import xp_sources


PUBLIC_AT = datetime(2026, 9, 21, 2, 45, tzinfo=timezone.utc)
FINAL_AT = datetime(2026, 9, 21, 18, tzinfo=timezone.utc)
PRACTICED_AT = datetime(2026, 9, 16, 18, tzinfo=timezone.utc)


@pytest.fixture
def history():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        season = Season(key='fall-2026', name='Fall 2026',
                        starts_on=date(2026, 9, 14), status='closed')
        session.add(season)
        session.flush()
        week = ContestWeek(
            season_id=season.id, week_start=date(2026, 9, 14),
            week_end=date(2026, 9, 21), status='finalized',
            verification_deadline_at=FINAL_AT - timedelta(hours=1),
            finalize_after=FINAL_AT, finalized_at=FINAL_AT,
        )
        session.add(week)
        session.commit()
        yield session, week
    engine.dispose()


def person(session, number=1, *, public_at=PUBLIC_AT):
    profile = WoodchuckProfile(
        woodchuck_id=f'WC-HISTORY-{number}', display_name=f'Historical Student {number}',
        pin_hash='unused', instrument='Flute', level='Beginner', goal='Practice',
    )
    session.add(profile)
    session.flush()
    declare_age(session, profile.id, 'adult', at=public_at)
    return profile


def saved_result(session, week, profile, *, kind='student', created_at=FINAL_AT):
    key = 'weekly-points-leaders' if kind == 'student' else 'weekly-practice-by-instrument'
    contest = session.scalar(select(Contest).where(Contest.key == key))
    if contest is None:
        contest = Contest(key=key, name=key, metric_type='practice_minutes', subject_type=kind)
        session.add(contest)
        session.flush()
    result = ContestResult(
        contest_week_id=week.id, contest_id=contest.id, division='open',
        subject_type=kind, subject_key=str(profile.id) if kind == 'student' else 'flute',
        profile_id=profile.id if kind == 'student' else None,
        instrument='Flute' if kind == 'instrument' else None,
        display_name_snapshot=profile.display_name if kind == 'student' else 'Flute',
        score=30, precise_score=30.5, rank=1, medal='gold', created_at=created_at,
    )
    session.add(result)
    session.flush()
    return result, contest


def reported_chart(session, profile, *, source='p-book'):
    chart = PracticeChart(
        profile_id=profile.id, practice_date=PRACTICED_AT.date(), minutes=30,
        instrument='Flute', source=source, include_contests=True,
        created_at=PRACTICED_AT,
        detected_playing_seconds=1830 if source == 'pristine' else None,
    )
    session.add(chart)
    session.flush()
    return chart


def visible(session, week, kind):
    medal = contests.contest_results_payload(session, week, _restore_legacy_students=True)
    hall = contests.hall_of_champions_payload(session)
    return (
        [row for row in medal['results'] if row['subject_type'] == kind],
        hall['students' if kind == 'student' else 'instruments'],
    )


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_finalized_student_visible_when_result_is_saved_before_at_or_after_screening(history, offset):
    session, week = history
    profile = person(session)
    saved_result(session, week, profile, created_at=PUBLIC_AT + timedelta(seconds=offset))
    session.commit()
    medal, hall = visible(session, week, 'student')
    assert [row['display_name'] for row in medal] == [profile.display_name]
    assert [row['display_name'] for row in hall] == [profile.display_name]
    assert medal[0]['score'] == 30.5
    assert hall[0]['medals']['gold'] == 1


@pytest.mark.parametrize('source', ['p-book', 'pristine'])
def test_finalized_instrument_keeps_unreviewed_original_sources_without_new_earning(history, source):
    session, week = history
    profile = person(session, public_at=PRACTICED_AT - timedelta(days=30))
    reported_chart(session, profile, source=source)
    saved_result(session, week, profile, kind='instrument')
    session.commit()
    medal, hall = visible(session, week, 'instrument')
    assert len(medal) == len(hall) == 1
    assert medal[0]['instrument'] == 'Flute'
    assert medal[0]['score'] == 30.5
    assert hall[0]['medals']['gold'] == 1
    # Historical display must not relax the separate earning/qualification query.
    assert contests._charts_and_approved_ids(session, week)[0] == []
    sources = xp_sources(session, profile_id=profile.id)
    assert sources['practice_minutes'] == sources['p_charts'] == 0


@pytest.mark.parametrize('kind', ['student', 'instrument'])
@pytest.mark.parametrize('privacy', ['missing', 'unknown', 'under13', 'unpublished', 'consent', 'deleted'])
def test_private_or_ineligible_history_is_not_restored(history, kind, privacy):
    session, week = history
    profile = person(session)
    rule = session.get(AccountPrivacy, profile.id)
    if privacy == 'missing':
        session.delete(rule)
    elif privacy in ('unknown', 'under13'):
        rule.age_band = privacy
        rule.public_from = None
    elif privacy == 'unpublished':
        rule.public_from = None
    elif privacy == 'consent':
        # Missing/revoked consent evidence must never qualify for legacy restoration.
        rule.consent_id = 123
    else:
        profile.status = 'deleted'
    if kind == 'instrument':
        reported_chart(session, profile)
    saved_result(session, week, profile, kind=kind)
    session.commit()
    assert visible(session, week, kind) == ([], [])


def test_unreviewed_private_contributor_cannot_be_dropped_from_instrument_privacy_check(history):
    session, week = history
    public = person(session, public_at=PRACTICED_AT - timedelta(days=30))
    private = person(session, 2)
    reported_chart(session, public)
    reported_chart(session, private)
    saved_result(session, week, public, kind='instrument')
    session.commit()
    assert visible(session, week, 'instrument') == ([], [])


@pytest.mark.parametrize('status', ['open', 'finalized-without-cutoff'])
def test_legacy_student_exception_requires_completed_finalization(history, status):
    session, week = history
    profile = person(session)
    row = saved_result(session, week, profile)
    week.status = 'open' if status == 'open' else 'finalized'
    week.finalized_at = None
    session.commit()
    assert filter_hall_result_rows(session, [row]) == []


def test_repeated_historical_reads_do_not_write_or_grant_rewards(history):
    session, week = history
    profile = person(session)
    contributor = person(session, 2, public_at=PRACTICED_AT - timedelta(days=30))
    reported_chart(session, contributor)
    saved_result(session, week, profile)
    saved_result(session, week, contributor, kind='instrument')
    session.add_all([
        WoodchuckState(profile_id=profile.id, state_json={'progress': {'credits': 17}}, revision=3),
        RewardGrant(profile_id=profile.id, source_key='original-award',
                    reward_type='dandelion', amount=7),
        CrownProgress(profile_id=profile.id, category_key='weekly-points-leaders', qualifying_wins=2),
    ])
    session.commit()

    def snapshot():
        return {table.name: list(session.execute(select(table).order_by(*table.primary_key.columns)))
                for table in Base.metadata.sorted_tables}

    before = snapshot()
    statements = []
    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(session.bind, 'before_cursor_execute', record_sql)
    try:
        for _ in range(3):
            for kind in ('student', 'instrument'):
                medal, hall = visible(session, week, kind)
                assert medal and hall
        assert not session.new and not session.dirty and not session.deleted
        session.commit()
    finally:
        event.remove(session.bind, 'before_cursor_execute', record_sql)
    assert set(statements) == {'SELECT'}
    assert snapshot() == before
