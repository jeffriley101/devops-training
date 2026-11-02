"""Actual desktop/mobile comprehensive reporting, with independent sharing sources."""
from datetime import datetime, time, timedelta
import socket
import re
from threading import Thread
import time as clock_time

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select
import uvicorn

from app import classroom_s2 as s2, classroom_reporting as reporting, classroom_progress_authorization as progress
from app import classroom_chart_sharing as sharing
from app.age_models import AccountPrivacy
from app.main import app
from app.models import PracticeChart, PracticeChartVerification
from app.student_practice_metrics import CENTRAL, week_start
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, institutional
from test_classroom_enrollment_ui import enrollment_web
from test_classroom_management_ui import management_web
from test_classroom_identity import identity_web
from test_classroom_director_workspace_browser import (workspace_browser_data,
    open_dashboard, SCHOOL_NAME, SHARED_NOTE, PRIVATE_NOTE, PUBLIC_NICKNAME)
from test_classroom_management_ui_browser import adult_context, snapshot
from test_classroom_enrollment_ui_browser import launch


@pytest.fixture
def comprehensive_browser_data(workspace_browser_data, monkeypatch):
    refs = workspace_browser_data
    clock = refs.workspace_clock
    with refs.factory() as session:
        # A synthetic institutional package permits five complete historical
        # rating weeks without extending or changing any production entitlement.
        institutional(session, starts_at=clock[0], ends_at=clock[0]+timedelta(days=365))
        session.commit()
        proof = s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468')
        choice = progress.choice(session, student=proof, program_id=1, class_id=refs.class_id)
        progress.accept(session, student=proof, program_id=1, class_id=refs.class_id,
            notice_version=progress.NOTICE_VERSION, assignment_ids=choice.assignment_ids,
            previous=choice.revision, source_revision=choice.source_revision, opted_in=True)
        session.commit()
        # Capture the new entitlement deliberately for the independent chart
        # selection as well; changing entitlement never revives an old share.
        proof = s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468')
        sharing.set_sharing(session, student=proof, chart_id=refs.workspace_chart_id,
            assignment_ids=[refs.workspace_assignment_id])
        session.commit()
    start = clock[0]
    clock[0] += timedelta(days=56)
    current = week_start(clock[0].astimezone(CENTRAL).date())
    refs.comprehensive_week = current.isoformat()
    with refs.factory() as session:
        for offset in range(1, 6):
            for day in range(5):
                date = current - timedelta(weeks=offset) + timedelta(days=day)
                instant = datetime.combine(date, time(12), CENTRAL)
                chart = PracticeChart(profile_id=1, practice_date=date, minutes=12+offset,
                    instrument='Trumpet', note='COMPREHENSIVE_PRIVATE_NOTE_NEVER_RETURN',
                    created_at=instant, source='p-book', practice_details=['Scales', 'Long Tones'])
                session.add(chart); session.flush()
                session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=1,
                    status='approved', response_note='COMPREHENSIVE_VERIFIER_NEVER_RETURN'))
        today = clock[0].astimezone(CENTRAL).date()
        chart = PracticeChart(profile_id=1, practice_date=today, minutes=23,
            instrument='Trumpet', created_at=clock[0], source='p-book',
            note='COMPREHENSIVE_PRIVATE_NOTE_NEVER_RETURN', practice_details=['Scales'])
        session.add(chart); session.flush()
        session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=1,status='approved'))
        # Both dates must qualify: this was submitted now for old private practice.
        session.add(PracticeChart(profile_id=1, practice_date=start.date()-timedelta(days=3),
            minutes=7777, instrument='Trumpet', created_at=clock[0],source='p-book',
            note='HISTORICAL_BACKFILL_SENTINEL'))
        session.commit()
    return refs


@pytest.fixture
def comprehensive_site(comprehensive_browser_data, monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
        monkeypatch.setenv('PUBLIC_BASE_URL',origin)
        server=uvicorn.Server(uvicorn.Config(app,log_level='warning',lifespan='off'))
        thread=Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
        try:
            for _ in range(100):
                if server.started: break
                assert thread.is_alive();clock_time.sleep(.05)
            assert server.started
            yield origin, comprehensive_browser_data
        finally:
            server.should_exit=True;thread.join(timeout=10)
            assert not thread.is_alive()


def assert_private_fields_absent(page):
    for value in (PUBLIC_NICKNAME, PRIVATE_NOTE, 'COMPREHENSIVE_PRIVATE_NOTE_NEVER_RETURN',
                  'COMPREHENSIVE_VERIFIER_NEVER_RETURN', 'HISTORICAL_BACKFILL_SENTINEL'):
        assert value not in page.content()
    assert 'profile_id' not in page.locator('#authorized-statistics').inner_html()


@pytest.mark.parametrize('engine', ['chromium','firefox'])
@pytest.mark.parametrize('width', [1440,390,320])
def test_comprehensive_fields_week_navigation_and_independent_withdrawal(comprehensive_site,tmp_path,engine,width):
    origin,refs=comprehensive_site;errors=[]
    with sync_playwright() as playwright:
        browser=launch(playwright,engine)
        context,page=adult_context(browser,origin,width,2,errors)
        open_dashboard(page,refs.workspace_student_path)
        stats=page.locator('#authorized-statistics')
        expect(stats).to_contain_text('Reporting authorization is currently valid')
        expect(stats.locator('[data-reporting-field]')).to_have_count(41)
        expect(stats.locator('.workspace-categories [data-reporting-field]')).to_have_count(23)
        expect(stats.locator('.workspace-graph')).to_have_count(2)
        expect(stats.locator('[data-reporting-field=student_practice_rating] dd')).to_have_text('76.6')
        expect(stats.locator('[data-reporting-field=rating_trend] .workspace-trend-down')).to_contain_text('-6.7')
        expect(page.locator('#administrative-records')).to_contain_text(SCHOOL_NAME)
        expect(page.locator('#shared-pcharts')).to_contain_text(SHARED_NOTE)
        assert_private_fields_absent(page)
        # No Program/Newsletter publishing controls or comprehensive notes/exports.
        assert stats.locator('button').all_text_contents()==['View reporting week']
        for label in ('Program Rating','Newsletter / Next Rating','Login streak','Game Mode',
                      'Rewards and achievements','Weekly attempts'):
            assert label not in stats.inner_text()
        snapshot(page,tmp_path/f'comprehensive-{engine}-{width}.png')
        prior=(datetime.fromisoformat(refs.comprehensive_week).date()-timedelta(weeks=1)).isoformat()
        page.locator('#reporting-week').fill(prior)
        page.locator('#reporting-week-pin').fill('2468')
        page.get_by_role('button',name='View reporting week',exact=True).click()
        expect(page.locator('#reporting-week')).to_have_value(prior)
        expect(stats.locator('[data-reporting-field]')).to_have_count(41)
        expect(stats.locator('[data-reporting-field=weekly_practice_minutes] dd')).to_have_text(re.compile(r'^65(?:\.0)? min$'))
        expect(stats.locator('[data-reporting-field=weekly_practice_days] dd')).to_have_text('5')
        expect(stats.locator('[data-reporting-field=weekly_practice_charts] dd')).to_have_text('5')
        expect(stats.locator('[data-reporting-field=category_scales] dd')).to_have_text('Reported')
        expect(stats.locator('[data-reporting-field=category_arpeggios] dd')).to_have_text('Not reported')
        expect(stats.locator('[data-reporting-field=weekly_practice_graph] tbody tr')).to_have_count(7)
        expect(stats.locator('.workspace-plot')).to_have_count(2)
        expect(stats.locator('[data-reporting-field=lifetime_practice_graph] polyline')).to_have_count(1)
        weekly_graph=stats.locator('[data-reporting-field=weekly_practice_graph]')
        weekly_graph.locator('summary').click()
        expect(weekly_graph.locator('table')).to_be_visible()
        expect(weekly_graph.locator('table')).to_contain_text('13.0')
        expect(stats.locator('[data-reporting-field=student_practice_rating] dd')).to_have_text('76.6')
        expect(stats.locator('[data-reporting-field=rating_trend] .workspace-trend-down')).to_contain_text('-6.7')
        assert_private_fields_absent(page)
        snapshot(page,tmp_path/f'comprehensive-completed-week-{engine}-{width}.png')
        # Withdrawing an individual chart never withdraws comprehensive access.
        with refs.factory() as session:
            proof=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468')
            sharing.set_sharing(session,student=proof,chart_id=refs.workspace_chart_id,assignment_ids=[])
            session.commit()
        open_dashboard(page,refs.workspace_student_path)
        expect(stats.locator('[data-reporting-field]')).to_have_count(41)
        expect(stats.locator('[data-reporting-field=student_practice_rating] dd')).to_have_text('76.6')
        expect(page.locator('#shared-pcharts')).not_to_contain_text(SHARED_NOTE)
        with refs.factory() as session:
            proof=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468')
            sharing.set_sharing(session,student=proof,chart_id=refs.workspace_chart_id,
                assignment_ids=[refs.workspace_assignment_id])
            session.commit()
        # Reporting withdrawal stops comprehensive reads, but selected charts
        # and private administrative identity retain their independent authority.
        with refs.factory() as session:
            proof=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468')
            progress.withdraw(session,student=proof,program_id=1,class_id=refs.class_id)
            session.commit()
        open_dashboard(page,refs.workspace_student_path)
        expect(stats.locator('[data-reporting-field]')).to_have_count(0)
        expect(stats).to_contain_text('unavailable')
        expect(page.locator('#administrative-records')).to_contain_text(SCHOOL_NAME)
        expect(page.locator('#shared-pcharts')).to_contain_text(SHARED_NOTE)
        assert_private_fields_absent(page)
        assert not errors
        browser.close()


@pytest.mark.parametrize('engine,width',[('chromium',1440),('firefox',390)])
def test_under13_reporting_is_unavailable_and_scope_substitution_denied(comprehensive_site,engine,width):
    origin,refs=comprehensive_site;errors=[]
    with sync_playwright() as playwright:
        browser=launch(playwright,engine)
        _,page=adult_context(browser,origin,width,2,errors)
        with refs.factory() as session:
            session.get(AccountPrivacy,1).age_band='under13';session.commit()
        open_dashboard(page,refs.workspace_student_path)
        expect(page.locator('#authorized-statistics [data-reporting-field]')).to_have_count(0)
        expect(page.locator('#authorized-statistics')).to_contain_text('unavailable')
        assert_private_fields_absent(page)
        for path in (f'/classroom/dashboard/1/classes/{refs.other_id}/students/{refs.member_id}',
                     f'/classroom/dashboard/2/classes/{refs.foreign_id}/students/{refs.member_id}'):
            open_dashboard(page,path,expected_status=404)
            assert SCHOOL_NAME not in page.content() and SHARED_NOTE not in page.content()
        assert not errors
        browser.close()
