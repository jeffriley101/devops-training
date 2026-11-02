"""Actual parent, student and director browsers; synthetic native KWS account only."""
from datetime import datetime, timedelta, timezone
import re
import socket
from threading import Thread
import time

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import func, select
import uvicorn

from app.main import app
from app import parent_access, classroom_identity as identity
from app.classroom_guardian_models import ClassroomGuardianAuthorization as Grant
from app.classroom_progress_authorization import LABEL
from app.models import PracticeChart
from app.student_practice_metrics import CENTRAL, week_start
from test_classroom_guardian_authorization import (guardian_data, migrated_db, captured, adult_pin_hash, actor)
from test_private_practice import path
from test_classroom_enrollment_ui_browser import launch
from test_classroom_director_workspace_browser import open_dashboard


@pytest.fixture
def guardian_site(guardian_data, monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        monkeypatch.setenv('PUBLIC_BASE_URL',origin)
        server = uvicorn.Server(uvicorn.Config(app,log_level='warning',lifespan='off'))
        thread = Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:break
                assert thread.is_alive();time.sleep(.05)
            assert server.started
            yield origin, guardian_data
        finally:
            server.should_exit=True;thread.join(timeout=10)
            assert not thread.is_alive()


def context(browser,origin,width,errors):
    result=browser.new_context(base_url=origin,viewport={'width':width,'height':950},reduced_motion='reduce')
    page=result.new_page();page.set_default_timeout(15000)
    page.on('pageerror',lambda error:errors.append(str(error)))
    return result,page


def parent_login(page,refs):
    refs.clock[0]+=timedelta(minutes=6)
    with refs.factory() as session:
        parent_access.request_link(session,woodchuck_id=refs.woodchuck_id,email='parent-a@example.test')
        session.commit()
    page.goto(path(refs.captured[0],'parent-access'),wait_until='domcontentloaded')
    page.get_by_role('button',name='Verify parent access',exact=True).click()
    page.get_by_role('link',name='Classroom parent permissions',exact=True).click()
    expect(page.get_by_role('heading',name='Classroom parent permissions',exact=True)).to_be_visible()


def parent_choose(page,purpose):
    section=page.locator(f'[data-parent-purpose={purpose}]')
    expect(section.locator('input[name=guardian_attestation]')).not_to_be_checked()
    expect(section.locator('input[name=opt_in]')).not_to_be_checked()
    section.locator('input[name=guardian_attestation]').check()
    section.locator('input[name=opt_in]').check()
    section.get_by_role('button',name='Authorize '+purpose,exact=True).click()
    expect(page.locator(f'[data-parent-purpose={purpose}] .parent-status')).to_contain_text('Permission is current')


def snapshot(page,target):
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    for button in page.locator('button:visible').all():
        assert button.inner_text().strip() and button.bounding_box()['height']>=44
    page.screenshot(path=str(target),full_page=True)


@pytest.mark.parametrize('migrated_db',['postgresql'],indirect=True)
@pytest.mark.parametrize('engine',['chromium','firefox'])
@pytest.mark.parametrize('width',[1440,390,320])
def test_parent_student_director_conjunction_and_withdrawal(guardian_site,tmp_path,engine,width):
    origin,refs=guardian_site;errors=[]
    with sync_playwright() as playwright:
        browser=launch(playwright,engine)
        parent_context,parent=context(browser,origin,width,errors)
        parent_login(parent,refs)
        expect(parent.locator('main')).to_contain_text(refs.woodchuck_id)
        expect(parent.locator('[data-parent-purpose=reporting]')).to_contain_text('Permission has not been given')
        assert parent.get_by_role('button',name='Authorize reporting',exact=True).count()==0
        parent.get_by_role('button',name='Authorize participation',exact=True).click()
        with refs.factory() as session:assert session.scalar(select(func.count(Grant.id)))==0
        snapshot(parent,tmp_path/f'parent-unchecked-{engine}-{width}.png')
        parent_choose(parent,'participation')
        reporting=parent.locator('[data-parent-purpose=reporting]')
        reporting.locator('summary').click()
        expect(reporting.locator('li')).to_have_count(41)
        parent_choose(parent,'reporting')
        snapshot(parent,tmp_path/f'parent-authorized-{engine}-{width}.png')
        child_context,child=context(browser,origin,width,errors)
        assert child_context.request.post('/account/login',form={'woodchuck_id':refs.woodchuck_id,'pin':'2468'},
            headers={'Origin':origin}).status==200
        child.goto('/classroom/reporting',wait_until='domcontentloaded')
        expect(child.get_by_role('checkbox',name=LABEL)).not_to_be_checked()
        child.get_by_role('checkbox',name=LABEL).check()
        child.get_by_role('button',name='Save authorization',exact=True).click()
        expect(child.get_by_role('button',name='Withdraw authorization',exact=True)).to_be_visible()
        # Practice in a later fully authorized week; both timestamps are eligible.
        selected=week_start(refs.clock[0].astimezone(CENTRAL).date())+timedelta(weeks=1)
        day=selected+timedelta(days=1)
        instant=datetime.combine(day,datetime.min.time(),CENTRAL).astimezone(timezone.utc)+timedelta(hours=18)
        with refs.factory() as session:
            identity.set_school_name(session,actor=actor(session,2),program_id=1,class_id=refs.class_id,
                woodchuck_id=refs.woodchuck_id,school_name='GUARDIAN PRIVATE SCHOOL NAME',expected_version=0)
            session.add(PracticeChart(profile_id=refs.profile_id,practice_date=day,created_at=instant,
                source='p-book',minutes=17,instrument='Flute',practice_details=['Scales'],note='GUARDIAN PRIVATE CHART NOTE'))
            session.commit()
        refs.clock[0]=datetime.combine(selected+timedelta(days=3),datetime.min.time(),CENTRAL).astimezone(timezone.utc)
        teacher_context,teacher=context(browser,origin,width,errors)
        assert teacher_context.request.post('/trusted-verifiers/login',form={'email':'adult2@example.test','pin':'2468'},
            headers={'Origin':origin}).status==200
        open_dashboard(teacher,refs.url)
        stats=teacher.locator('#authorized-statistics')
        expect(stats.locator('[data-reporting-field]')).to_have_count(41)
        expect(stats.locator('[data-reporting-field=lifetime_practice_minutes] dd')).to_have_text(re.compile(r'^17(?:\.0)? min$'))
        expect(stats.locator('[data-reporting-field=weekly_practice_minutes] dd')).to_have_text(re.compile(r'^17(?:\.0)? min$'))
        expect(stats.locator('.workspace-plot')).to_have_count(2)
        expect(teacher.locator('#administrative-records')).to_contain_text('GUARDIAN PRIVATE SCHOOL NAME')
        assert 'GUARDIAN PRIVATE CHART NOTE' not in teacher.content()
        expect(teacher.locator('#shared-pcharts')).not_to_contain_text('GUARDIAN PRIVATE CHART NOTE')
        snapshot(teacher,tmp_path/f'guardian-director-report-{engine}-{width}.png')
        # Parent permission is durable; native parent browser credentials still expire.
        parent_login(parent,refs)
        report_section=parent.locator('[data-parent-purpose=reporting]')
        report_section.locator('input[name=confirm]').check()
        report_section.get_by_role('button',name='Withdraw reporting',exact=True).click()
        expect(parent.locator('[data-parent-purpose=participation] .parent-status')).to_contain_text('Permission is current')
        open_dashboard(teacher,refs.url)
        expect(stats.locator('[data-reporting-field]')).to_have_count(0)
        expect(stats).to_contain_text('unavailable')
        expect(teacher.locator('#administrative-records')).to_contain_text('GUARDIAN PRIVATE SCHOOL NAME')
        assert 'GUARDIAN PRIVATE CHART NOTE' not in teacher.content()
        snapshot(parent,tmp_path/f'parent-reporting-withdrawn-{engine}-{width}.png')
        assert not errors
        browser.close()


@pytest.mark.parametrize('migrated_db',['postgresql'],indirect=True)
@pytest.mark.parametrize('engine,width',[('chromium',1440),('firefox',390)])
def test_director_is_not_parent_and_school_assertion_never_qualifies(guardian_site,engine,width):
    origin,refs=guardian_site;errors=[]
    with sync_playwright() as playwright:
        browser=launch(playwright,engine)
        unauthorized,page=context(browser,origin,width,errors)
        assert unauthorized.request.post('/trusted-verifiers/login',form={'email':'adult2@example.test','pin':'2468'},
            headers={'Origin':origin}).status==200
        response=page.goto('/classroom/parent')
        assert response.status==403 and refs.woodchuck_id not in page.content()
        parent_context,parent=context(browser,origin,width,errors)
        parent_login(parent,refs)
        form=parent.locator('[data-parent-purpose=participation] form')
        data={item['name']:item['value'] for item in form.locator('input[type=hidden]').evaluate_all(
            '(nodes)=>nodes.map(n=>({name:n.name,value:n.value}))')}
        data.update(guardian_attestation='yes',opt_in='yes')
        target=f'/classroom/parent/1/classes/{refs.class_id}/school/authorize'
        assert parent_context.request.post(target,form=data,headers={'Origin':origin}).status==409
        target=f'/classroom/parent/1/classes/{refs.class_id}/participation/authorize'
        assert parent_context.request.post(target,form={**data,'school_email':'teacher@school.example.test'},
            headers={'Origin':origin}).status==400
        with refs.factory() as session:assert session.scalar(select(func.count(Grant.id)))==0
        assert not errors
        browser.close()
