"""Local synthetic school workflow and explicit private chart selection browsers."""
from datetime import timedelta

import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import func, select

from app import classroom_school_authorization as school
from app.classroom_school_models import ClassroomSchoolAuthorization as Approval
from app.classroom_models import ClassroomChartDirectorShare
from test_classroom_guardian_browser import guardian_site, guardian_data, migrated_db, captured, adult_pin_hash
from test_classroom_guardian_browser import context, snapshot
from test_classroom_enrollment_ui_browser import launch
from test_classroom_director_workspace_browser import open_dashboard
from test_classroom_school_chart_privacy import proposed_chart_data


@pytest.fixture
def school_site(guardian_site, monkeypatch):
    monkeypatch.setenv('CLASSROOM_SCHOOL_AUTHORIZATION_ENABLED', '1')
    return guardian_site


def sign_in(ctx, origin, email, pin):
    assert ctx.request.post('/trusted-verifiers/login', form={'email':email,'pin':pin},
        headers={'Origin':origin}).status == 200


def open_school(page, path):
    page.goto(path, wait_until='domcontentloaded')
    page.locator('input[name=pin]').fill('2468')
    page.get_by_role('button', name='Open school agreement', exact=True).click()
    expect(page.get_by_role('heading', name='Synthetic Program 1', exact=True)).to_be_visible()


def fill_verification(page, refs, kind, *, year_index=0):
    form = page.locator('form:has(input[name=approver_email])')
    form.locator('select[name=year_token]').select_option(index=year_index)
    form.locator('select[name=authority_kind]').select_option(kind)
    form.locator('select[name=verification_method]').select_option('phone')
    values = {'approver_email':f"adult{2 if kind == 'director' else 3}@example.test",
        'school_name':'Synthetic Browser School', 'authority_description':'Official principal confirmed approval authority for Music Program 1.',
        'contact_name':'Synthetic Independent Principal', 'contact_role':'Principal', 'contact_address':'+1 555 010 1234',
        'contact_source':'https://school.example.test/official-directory',
        'contacted_at':(refs.clock[0]-timedelta(minutes=5)).isoformat(),
        'verification_notes':'Synthetic documented call: principal confirmed the named approver and exact Program scope.'}
    for key, value in values.items(): form.locator(f'input[name={key}]').fill(value)
    form.locator('input[name=pin]').fill('2468')
    return form


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('engine,width', [('chromium',1440), ('firefox',390)])
@pytest.mark.parametrize('kind', ['director','administrator'])
def test_school_phone_evidence_common_approval_renewal_and_revocation(school_site, tmp_path, engine, width, kind):
    origin,refs = school_site; errors=[]
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        staff_ctx, staff = context(browser, origin, width, errors)
        sign_in(staff_ctx, origin, 'adult1@example.test', '2468')
        staff.goto('/admin/login')
        csrf = staff.locator('input[name=csrf]').input_value()
        assert staff_ctx.request.post('/admin/login', form={'csrf':csrf,'token':'synthetic-classroom-s2-admin'},
            headers={'Origin':origin}, max_redirects=0).status == 303
        open_school(staff, '/admin/classroom/school/1')
        form = fill_verification(staff, refs, kind)
        expect(form.locator('input[name=independently_confirmed]')).not_to_be_checked()
        form.get_by_role('button', name='Record authority verification', exact=True).click()
        with refs.factory() as session: assert session.scalar(select(func.count(Approval.id))) == 0
        form.locator('input[name=independently_confirmed]').check()
        form.get_by_role('button', name='Record authority verification', exact=True).click()
        expect(staff.get_by_role('status')).to_contain_text('Authority evidence recorded')
        staff.locator('summary').click()
        snapshot(staff, tmp_path/f'school-verified-{kind}-{engine}-{width}.png')
        approver_ctx, approver = context(browser, origin, width, errors)
        sign_in(approver_ctx, origin, f"adult{2 if kind=='director' else 3}@example.test", '2468')
        open_school(approver, '/classroom/school/1')
        assert 'Synthetic Independent Principal' not in approver.content()
        record = approver.locator('[data-school-record]').first
        form = record.locator('form:has(input[name=approve])')
        expect(form.locator('input[name=approve]')).not_to_be_checked()
        form.locator('input[name=approve]').check(); form.locator('input[name=pin]').fill('2468')
        form.get_by_role('button', name='Approve school agreement', exact=True).click()
        expect(approver.get_by_role('status')).to_contain_text('Legal review is pending')
        expect(record).to_contain_text('Legal review pending')
        with refs.factory() as session:
            assert session.scalar(select(Approval.approved_at)) is not None
            assert not school.authorized_program(session, program_id=1)
            assert session.scalar(select(func.count(ClassroomChartDirectorShare.id))) == 0
        snapshot(approver, tmp_path/f'school-agreement-{kind}-{engine}-{width}.png')
        # The same form records a fresh next-year verification and explicit approval.
        open_school(staff, '/admin/classroom/school/1')
        renew = fill_verification(staff, refs, kind, year_index=1)
        renew.locator('input[name=independently_confirmed]').check()
        renew.get_by_role('button', name='Record authority verification', exact=True).click()
        open_school(approver, '/classroom/school/1')
        renewed = approver.locator('[data-school-record]').first
        form = renewed.locator('form:has(input[name=approve])')
        form.locator('input[name=approve]').check(); form.locator('input[name=pin]').fill('2468')
        form.get_by_role('button', name='Approve school agreement', exact=True).click()
        with refs.factory() as session:
            rows = session.scalars(select(Approval).order_by(Approval.id)).all()
            assert len(rows) == 2 and rows[1].year_start == rows[0].year_start+1
            assert all(row.approved_at for row in rows)
        revoke = approver.locator('[data-school-record]').first.locator('form:has(input[name=confirm])')
        revoke.locator('select[name=reason]').select_option('authority_revoked')
        revoke.locator('input[name=confirm]').check(); revoke.locator('input[name=pin]').fill('2468')
        revoke.get_by_role('button', name='Revoke school agreement', exact=True).click()
        expect(approver.locator('[data-school-record]').first).to_contain_text('Revoked')
        assert not errors
        browser.close()


@pytest.fixture
def private_chart_site(guardian_site, proposed_chart_data):
    return guardian_site


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('guardian_data', [{'under13':True}, {'under13':False}], indirect=True)
@pytest.mark.parametrize('engine,width', [('chromium',1440), ('firefox',390)])
def test_private_chart_student_choice_and_withdrawal_across_ages(private_chart_site, tmp_path, engine, width):
    origin,refs = private_chart_site; errors=[]
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        child_ctx,child = context(browser, origin, width, errors)
        assert child_ctx.request.post('/account/login', form={'woodchuck_id':refs.woodchuck_id,'pin':'2468'},
            headers={'Origin':origin}).status == 200
        child.goto('/family/practice', wait_until='domcontentloaded')
        card = child.locator(f'[data-classroom-chart="{refs.chart_id}"]')
        card.get_by_role('button', name='Edit sharing', exact=True).click()
        check = card.get_by_role('checkbox', name='Send this P-Chart to my Band Director', exact=True)
        expect(check).not_to_be_checked()
        check.check(); card.get_by_label('Choose a director and Class', exact=True).select_option(str(refs.private_assignment_id))
        card.get_by_role('button', name='Save sharing', exact=True).click()
        expect(card.get_by_role('status')).to_contain_text('Sharing saved')
        teacher_ctx,teacher = context(browser, origin, width, errors)
        sign_in(teacher_ctx, origin, 'director-a@example.test', '1357')
        # Existing workspace requires the current recipient's PIN each time.
        teacher.goto(refs.url)
        teacher.locator('input[name=pin]').fill('1357')
        teacher.get_by_role('button', name='Open dashboard', exact=True).click()
        expect(teacher.locator('#shared-pcharts')).to_contain_text('EXPLICIT_PRIVATE_CHART')
        expect(teacher.locator('#authorized-statistics')).to_contain_text('unavailable')
        assert 'UNSELECTED_PRIVATE_CHART' not in teacher.content()
        snapshot(child,tmp_path/f'private-chart-sharing-{engine}-{width}.png')
        check.uncheck(); card.get_by_role('button', name='Save sharing', exact=True).click()
        expect(card.get_by_role('status')).to_contain_text('Sharing saved')
        teacher.goto(refs.url); teacher.locator('input[name=pin]').fill('1357')
        teacher.get_by_role('button', name='Open dashboard', exact=True).click()
        expect(teacher.locator('#shared-pcharts')).not_to_contain_text('EXPLICIT_PRIVATE_CHART')
        assert not errors
        browser.close()
