"""Stored instrument medals use only their own canonical instrument sources."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import contests
from app.age_privacy import declare_age, filter_result_rows, filter_hall_result_rows
from app.models import Contest, ContestResult, PracticeChartVerification
from tests.test_team_contests import add_chart, add_profile
from tests.test_teams import profile
from tests.test_teams_medal_hotfix import s, season, week, NOW  # noqa: F401


def stored_result(session, contest_week, instrument, rank):
    contest = session.scalar(select(Contest).where(Contest.key == 'weekly-practice-by-instrument'))
    if contest is None:
        contest = Contest(key='weekly-practice-by-instrument', name='Instrument practice',
                          metric_type='practice_minutes', subject_type='instrument')
        session.add(contest); session.flush()
    key, label = contests.normalize_instrument(instrument)
    result = ContestResult(contest_week_id=contest_week.id, contest_id=contest.id,
        division='open', subject_type='instrument', subject_key=key,
        instrument=instrument, display_name_snapshot=label,
        score=30, precise_score=30.5, rank=rank, medal='gold', created_at=NOW)
    session.add(result); session.flush()
    return result, contest


@pytest.mark.parametrize('sources,instruments,visible', [
    ([('Trumpet', True), ('Clarinet', False)], ['Trumpet', 'Clarinet'], ['Trumpet']),
    ([('Saxophone', True), ('Saxophone', False)], ['Saxophone'], []),
    ([('Trumpet', True), ('Trumpet', True)], ['Trumpet'], ['Trumpet']),
    ([('Clarinet', True)], ['Trumpet'], []),
    ([], ['Trumpet'], []),
    ([('Trumpet', False), ('Clarinet', False), ('Saxophone', False)],
     ['Trumpet', 'Clarinet', 'Saxophone'], []),
    ([('Piano', True), ('Keyboard', False)], ['Piano / Keyboard'], []),
    ([('Piano', True), ('Keyboard', True)], ['piano-keyboard'], ['piano-keyboard']),
    ([('  TRUMPET  ', True)], ['Trumpet'], ['Trumpet']),
])
def test_instrument_sources_are_isolated_and_history_is_unchanged(s, sources, instruments, visible):
    active = season(s)
    finalized = week(s, active, status='finalized')  # Sept. 14–20, 2026
    public = add_profile(s, 1)
    private = profile(s, 2)
    declare_age(s, private.id, 'under13', at=NOW-timedelta(days=30))
    for instrument, is_public in sources:
        chart = add_chart(s, public if is_public else private, None, 30, approved=True,
                          practice_date=NOW.date(), created_at=NOW)
        chart.instrument = instrument
    rows = [stored_result(s, finalized, instrument, rank)
            for rank, instrument in enumerate(instruments, 1)]
    s.commit()
    stored_rows = select(ContestResult.__table__).order_by(ContestResult.id)
    before = list(s.execute(stored_rows))
    for filter_rows in (filter_result_rows, filter_hall_result_rows):
        assert [r.instrument for r, _ in filter_rows(s, rows)] == visible
    for restore in (False, True):
        payload = contests.contest_results_payload(s, finalized, _restore_legacy_students=restore)
        assert [r['instrument'] for r in payload['results']] == [
            contests.normalize_instrument(instrument)[1] for instrument in visible
        ]
        for row in payload['results']:
            assert not {'profile_id', 'members', 'contributors', 'contributor_count', 'practice_records'} & row.keys()
            assert row['active_member_count'] is None
            assert row['score'] == 30.5
        assert public.display_name not in repr(payload) and private.display_name not in repr(payload)
    assert list(s.execute(stored_rows)) == before
    assert finalized.status == 'finalized'
    assert not s.new and not s.dirty and not s.deleted


@pytest.mark.parametrize('matching_public', [False, True])
def test_ineligible_and_outside_week_charts_do_not_control_visibility(s, matching_public):
    active = season(s)
    finalized = week(s, active, status='finalized')
    public = add_profile(s, 1)
    private = profile(s, 2)  # Unknown-age charts must also stay private.
    for day, included in [(finalized.week_start-timedelta(days=1), True),
                          (finalized.week_end, True), (NOW.date(), False)]:
        chart = add_chart(s, private, None, 30, approved=True, practice_date=day, created_at=NOW)
        chart.instrument = 'Trumpet'
        chart.include_contests = included
    if matching_public:
        chart = add_chart(s, public, None, 30, approved=True,
                          practice_date=finalized.week_start, created_at=NOW)
        chart.instrument = 'Trumpet'
    row = stored_result(s, finalized, 'Trumpet', 1)
    # Historical snapshots can retain only the display label.
    row[0].instrument = None
    s.commit()
    assert filter_result_rows(s, [row]) == ([row] if matching_public else [])


@pytest.mark.parametrize('division,private_approval,visible', [
    ('verified', None, True),
    ('verified', 'pending', True),
    ('verified', 'rejected', True),
    ('verified', 'late', True),
    ('verified', 'missing-time', True),
    ('verified', 'approved', False),
    ('open', None, False),  # Original private contributions still protect saved medals.
    ('pristine', None, False),
])
def test_historical_division_uses_scoring_approval_deadline(s, division, private_approval, visible):
    finalized = week(s, season(s), status='finalized')
    public = add_profile(s, 1)
    private = profile(s, 2)
    for person, approval in ((public, 'approved'), (private, private_approval)):
        chart = add_chart(s, person, None, 30, approved=False,
                          practice_date=NOW.date(), created_at=NOW)
        chart.instrument = 'Trumpet'
        if approval is not None:
            responded = finalized.verification_deadline_at
            if approval == 'late': responded += timedelta(microseconds=1)
            if approval == 'missing-time': responded = None
            s.add(PracticeChartVerification(practice_chart_id=chart.id,
                status=approval if approval in ('pending', 'rejected') else 'approved',
                responded_at=responded))
    row = stored_result(s, finalized, 'Trumpet', 1)
    row[0].division = division
    s.commit()
    before = list(s.execute(select(ContestResult.__table__)))
    assert filter_result_rows(s, [row]) == ([row] if visible else [])
    assert filter_hall_result_rows(s, [row]) == ([row] if visible else [])
    assert bool(contests.contest_results_payload(s, finalized)['results']) == visible
    assert list(s.execute(select(ContestResult.__table__))) == before


@pytest.mark.parametrize('division', ['open', 'verified'])
@pytest.mark.parametrize('original_source', [False, True])
def test_post_finalization_charts_cannot_change_historical_visibility(s, division, original_source):
    finalized = week(s, season(s), status='finalized')
    public = add_profile(s, 1)
    private = profile(s, 2)
    def source(person, submitted):
        chart = add_chart(s, person, None, 30, approved=False,
                          practice_date=NOW.date(), created_at=submitted)
        chart.instrument = 'Trumpet'
        s.add(PracticeChartVerification(practice_chart_id=chart.id, status='approved',
              responded_at=finalized.verification_deadline_at))
    if original_source:
        source(public, finalized.finalized_at)  # Inclusive scoring cutoff.
    row = stored_result(s, finalized, 'Trumpet', 1)
    row[0].division = division
    s.commit()
    expected = [row] if original_source else []
    assert filter_result_rows(s, [row]) == expected
    before = list(s.execute(select(ContestResult.__table__)))
    # Neither a private late chart nor a public late chart establishes or
    # changes original provenance, even with backdated practice/approval dates.
    for person in (private, public):
        source(person, finalized.finalized_at + timedelta(microseconds=1))
        s.commit()
        assert filter_result_rows(s, [row]) == expected
        assert filter_hall_result_rows(s, [row]) == expected
    assert list(s.execute(select(ContestResult.__table__))) == before


def test_verified_requires_at_least_one_approved_matching_source(s):
    finalized = week(s, season(s), status='finalized')
    public = add_profile(s, 1)
    for instrument, approved in [('Trumpet', False), ('Clarinet', True)]:
        chart = add_chart(s, public, None, 30, approved=False,
                          practice_date=NOW.date(), created_at=NOW)
        chart.instrument = instrument
        if approved:
            s.add(PracticeChartVerification(practice_chart_id=chart.id, status='approved',
                  responded_at=finalized.verification_deadline_at))
    row = stored_result(s, finalized, 'Trumpet', 1)
    row[0].division = 'verified'
    s.commit()
    assert filter_result_rows(s, [row]) == []
