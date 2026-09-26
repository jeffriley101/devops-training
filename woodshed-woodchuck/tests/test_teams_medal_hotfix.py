"""Identity-only Team listings and family/medal projections with private members."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app import contests, teams
from app.age_privacy import declare_age, team_public, public_team_identity_allowed
from app.db import Base
from app.models import (Season, Team, TeamFamily, TeamMembership, ContestWeek, Contest,
                        ContestResult, PracticeChart)
from tests.test_team_contests import database, add_profile, add_chart
from tests.test_teams import profile
from tests.team_factory import make_team
from tests.test_team_families import disposable_url

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


@pytest.fixture(params=['sqlite', 'postgresql'])
def s(request,tmp_path):
    if request.param == 'sqlite':
        session = database()
    else:
        engine = create_engine(disposable_url(tmp_path, 'postgresql'))
        Base.metadata.create_all(engine)
        session = Session(engine, expire_on_commit=False)
    yield session
    session.close()
    session.bind.dispose()


def season(s, key='current', start=date(2026, 9, 14), end=None):
    row = Season(key=key, name=key, starts_on=start, ends_on=end, status='active')
    s.add(row); s.flush()
    return row


def team(s, active, name, emblem, **values):
    row = make_team(s, season_id=active.id, display_name=name, normalized_name=name.casefold(),
                    emblem_key=emblem, **values)
    s.add(row); s.flush()
    return row


def week(s, active, start=date(2026, 9, 14), status='open'):
    row = ContestWeek(season_id=active.id, week_start=start, week_end=start+timedelta(days=7),
                      verification_deadline_at=NOW+timedelta(days=7), finalize_after=NOW+timedelta(days=7),
                      status=status, finalized_at=NOW+timedelta(days=7) if status=='finalized' else None,
                      practice_scoring_mode='precise_seconds')
    s.add(row); s.flush()
    return row


def result(s, w, subject, kind='team', key='team-lifetime-practice', rank=1):
    contest = s.scalar(select(Contest).where(Contest.key == key))
    if contest is None:
        contest = Contest(key=key, name=key, metric_type='practice_minutes', subject_type=kind)
        s.add(contest); s.flush()
    row = ContestResult(contest_week_id=w.id, contest_id=contest.id, division='open',
        subject_type=kind, subject_key=str(subject.id) if subject else 'flute',
        team_id=subject.id if kind=='team' else None, profile_id=subject.id if kind=='student' else None,
        instrument='Flute' if kind=='instrument' else None,
        display_name_snapshot=subject.display_name if subject else 'Flute',
        score=30, precise_score=30.5, rank=rank, medal={1:'gold',2:'silver',3:'bronze'}[rank],
        active_member_count=4 if kind=='team' else None, created_at=NOW)
    s.add(row); s.flush()
    return row


def test_full_safe_chooser_pool_with_private_members_and_locked_selection(s, monkeypatch):
    active = season(s)
    child, unknown, chooser = [profile(s, number) for number in (1,2,3)]
    declare_age(s, child.id, 'under13', at=NOW-timedelta(days=30))
    declare_age(s, chooser.id, 'adult', at=NOW-timedelta(days=30))
    public = [team(s, active, f'Team {i:03}', f'letter:test-{i}') for i in range(205)]
    public[0].emblem_key='emoji:cat'; public[0].creator_profile_id=child.id
    public[1].emblem_key='emoji:dog'; public[1].creator_profile_id=unknown.id
    hidden = team(s, active, 'Hidden', 'shield:red', moderation_status='hidden')
    classroom = team(s, active, 'Class One', 'shield:blue', visibility='private', director_led=True, join_code='CLASS001')
    for who,t in [(child,public[0]),(unknown,public[1])]:
        s.add(TeamMembership(season_id=active.id, team_id=t.id, profile_id=who.id,
                            selected_week_start=date(2026,9,14), started_at=NOW))
    s.commit()
    teams.select_team(s, profile=chooser, season=active, team=public[0], now=NOW); s.commit()
    teams.select_team(s, profile=chooser, season=active, team=public[1], now=NOW+timedelta(hours=1)); s.commit()
    assert not team_public(s,public[0].id) and not team_public(s,public[1].id)
    payload=teams.selection_payload(s,profile=chooser,now=NOW+timedelta(hours=2))
    assert payload['membership']['locked'] is True
    assert {t['id'] for t in payload['teams']} == {t.id for t in public}
    assert all(set(t)=={'id','name','emblem'} for t in payload['teams'])
    assert all(t['emblem']['key'] in teams.APPROVED_EMBLEMS for t in payload['teams'])
    assert payload['teams'][0]['name']=='Team 000' and payload['teams'][0]['emblem']['key']=='emoji:cat'
    assert all(p.display_name not in repr(payload['teams']) for p in [child,unknown,chooser])
    with pytest.raises(ValueError,match='locked'):
        teams.select_team(s,profile=chooser,season=active,team=public[2],now=NOW+timedelta(hours=2))
    assert not public_team_identity_allowed(hidden)
    assert not public_team_identity_allowed(classroom)
    assert not public_team_identity_allowed(None)
    monkeypatch.setattr(teams, 'SessionLocal', lambda:s)
    monkeypatch.setattr(teams, 'authenticated_context', lambda request,session:(chooser,active,NOW+timedelta(hours=2)))
    request=Request({'type':'http','method':'GET','path':'/teams','headers':[],
                     'session':{'woodchuck_profile_id':chooser.id}})
    assert teams.list_teams(request)['teams']==payload['teams']


def test_lifetime_family_totals_identity_cutoffs_privacy_and_ties(s):
    old=season(s,'old',date(2026,7,27),date(2026,9,13)); active=season(s)
    owner=add_profile(s,1); child=profile(s,2); declare_age(s,child.id,'under13',at=NOW-timedelta(days=60))
    first=team(s,old,'Union','emoji:cat')
    current=Team(season_id=active.id,family_id=first.family_id,display_name='Union Renewed',normalized_name='union renewed',emblem_key='emoji:cat')
    s.add(current);s.flush()
    other=team(s,active,'Other','emoji:dog')
    classroom=team(s,old,'Class One','letter:C',visibility='private',director_led=True,join_code='CLASS001')
    historical=team(s,old,'History','letter:H')
    future_season=season(s,'future',date(2026,10,5))
    future=Team(season_id=future_season.id,family_id=historical.family_id,display_name='Future',normalized_name='future',emblem_key='letter:H')
    s.add(future);s.flush()
    w=week(s,active)
    for t,person,seconds,day,created in [
        (first,owner,605,date(2026,8,1),NOW-timedelta(days=40)),
        (current,owner,1205,NOW.date(),NOW), (other,owner,1810,NOW.date(),NOW),
        (classroom,owner,9999,NOW.date(),NOW), (historical,owner,600,NOW.date(),NOW),
        (first,child,9999,NOW.date(),NOW), (current,owner,9999,NOW.date(),NOW+timedelta(days=1)),
        (current,owner,9999,w.week_end,NOW),
    ]:
        add_chart(s,person,t.id,seconds//60,approved=True,practice_date=day,created_at=created)
    s.commit()
    scores=contests._lifetime_team_practice_scores(s,w,source_cutoff=NOW,public_only=True)
    assert scores=={current.id:30,other.id:30,historical.id:10}
    rows=contests.team_leaderboards(s,season=active,contest_week=w,source_cutoff=NOW)['team-lifetime-practice']['open']
    assert [r['rank'] for r in rows]==[1,1,3]
    assert next(r for r in rows if r['team_id']==current.id)['team_name']=='Union Renewed'
    current.moderation_status='hidden';s.commit()
    assert current.id not in contests._lifetime_team_practice_scores(s,w,source_cutoff=NOW,public_only=True)
    assert first.id not in contests._lifetime_team_practice_scores(s,w,source_cutoff=NOW,public_only=True)


def test_medal_projection_keeps_public_team_aggregate_and_student_instrument_privacy(s,monkeypatch):
    active=season(s); w=week(s,active,status='finalized')
    child,unknown,legacy=[profile(s,n) for n in (1,2,3)]
    declare_age(s,child.id,'under13',at=NOW-timedelta(days=30))
    declare_age(s,legacy.id,'adult',at=NOW+timedelta(days=10))
    public=team(s,active,'Union','emoji:cat',creator_profile_id=child.id)
    hidden=team(s,active,'Hidden','emoji:dog',moderation_status='hidden')
    classroom=team(s,active,'Class One','letter:C',visibility='private',director_led=True,join_code='CLASS001')
    for p in [child,unknown]:
        s.add(TeamMembership(season_id=active.id,team_id=public.id,profile_id=p.id,selected_week_start=w.week_start,started_at=NOW))
    saved=[result(s,w,t) for t in [public,hidden,classroom]]
    saved += [result(s,w,p,'student','weekly-points-leaders') for p in [child,unknown,legacy]]
    saved += [result(s,w,None,'instrument','weekly-practice-by-instrument')]
    add_chart(s,child,public.id,30,approved=False,practice_date=NOW.date(),created_at=NOW)
    s.commit()
    before=[tuple(getattr(r,c.name) for c in ContestResult.__table__.columns) for r in saved]
    monkeypatch.setattr(contests,'SessionLocal',lambda:s)
    request=Request({'type':'http','method':'GET','path':'/contests/weeks/2026-09-14/results','headers':[],
                     'session':{'woodchuck_profile_id':legacy.id}})
    payload=contests.contest_week_results(w.week_start,request)
    assert [(r['subject_type'],r.get('team_name',r.get('display_name'))) for r in payload['results']]==[('team','Union'),('student',legacy.display_name)]
    public_row=payload['results'][0]
    assert (public_row['rank'],public_row['medal'],public_row['score'])==(1,'gold',30.5)
    assert public_row['active_member_count'] is None
    assert not {'profile_id','members','practice_records'} & public_row.keys()
    assert child.display_name not in repr(payload) and unknown.display_name not in repr(payload)
    assert before==[tuple(getattr(r,c.name) for c in ContestResult.__table__.columns) for r in saved]


def test_hall_combines_family_and_current_coterie_uses_family_after_rename(s):
    old=season(s,'old',date(2026,7,27),date(2026,9,13)); active=season(s)
    oldteam=team(s,old,'Union','emoji:cat'); member=add_profile(s,1)
    current=Team(season_id=active.id,family_id=oldteam.family_id,display_name='Renamed Union',normalized_name='renamed union',emblem_key='emoji:cat')
    s.add(current);s.flush()
    oldweek=week(s,old,date(2026,9,7),'finalized'); currentweek=week(s,active,status='finalized')
    result(s,oldweek,oldteam);result(s,currentweek,current)
    # Existing history can refer to both seasonal IDs within the same week.
    result(s,currentweek,oldteam,key='team-weekly-practice')
    s.add(TeamMembership(season_id=active.id,team_id=current.id,profile_id=member.id,selected_week_start=currentweek.week_start,started_at=NOW-timedelta(days=1)))
    s.commit()
    hall=contests.hall_of_champions_payload(s,now=NOW)
    assert len(hall['teams'])==1 and hall['teams'][0]['medals']['gold']==3
    assert contests._current_team_member_ids(s,{'_family_id':oldteam.family_id,'_normalized_name':'wrong','_owner_profile_id':999},now=NOW)=={member.id}
    assert contests._current_team_member_ids(s,{'team_id':999},now=NOW)==set()


def test_finalized_empty_week_remains_listed(s):
    active=season(s);w=week(s,active,status='finalized')
    hidden=team(s,active,'Hidden','emoji:cat',moderation_status='hidden')
    result(s,w,hidden);s.commit()
    assert contests.contest_results_payload(s,w)['results']==[]
    assert contests.finalized_weeks_payload(s)['weeks'][0]['week_start']=='2026-09-14'


@pytest.mark.parametrize('mode,expected', [('precise_seconds',30),('legacy_minutes',30)])
def test_finalization_creates_one_lifetime_result_per_family(s,mode,expected):
    old=season(s,'old',date(2026,7,27),date(2026,9,13)); active=season(s)
    owner=add_profile(s,1)
    first=team(s,old,'Union','emoji:cat')
    current=Team(season_id=active.id,family_id=first.family_id,display_name='Union',normalized_name='union',emblem_key='emoji:cat')
    s.add(current);s.flush()
    w=week(s,active);w.practice_scoring_mode=mode
    contests.ensure_contest_definitions(s)
    for t,seconds,day in [(first,605,date(2026,8,1)),(current,1205,NOW.date())]:
        add_chart(s,owner,t.id,seconds//60,approved=True,practice_date=day,created_at=NOW)
    s.commit()
    contests.finalize_contest_week(s,week_start=w.week_start,now=NOW+timedelta(days=8));s.commit()
    rows=list(s.scalars(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id==w.id,Contest.key=='team-lifetime-practice')))
    assert len(rows)==1
    assert (rows[0].team_id,rows[0].subject_key,rows[0].rank)==(current.id,str(current.id),1)
    assert rows[0].effective_score==pytest.approx(expected)
    assert w.practice_scoring_mode==mode
