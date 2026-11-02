"""Real native student browsers, PostgreSQL and explicit Weekly Loop Homework."""
from datetime import timedelta

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select

from app import classroom_guardian_authorization as guardian, child_authorization
from app.child_models import ConsentEvidence
from app.feature_access import FEATURES, Feature
from app.models import WoodchuckProfile, PracticeChart
from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan
from test_classroom_student_home import (
    student_home, guardian_data, migrated_db, captured, adult_pin_hash,
    allowed_class, parent_proof, rows, FOCI,
)
from test_classroom_guardian_browser import guardian_site, context, snapshot
from test_classroom_enrollment_ui_browser import launch


@pytest.fixture
def student_site(guardian_site, student_home):
    origin, refs = guardian_site
    assert refs is student_home
    return origin, refs


def login(context, origin, refs):
    response = context.request.post('/account/login', form={'woodchuck_id':refs.woodchuck_id,'pin':'2468'},
        headers={'Origin':origin})
    assert response.status == 200


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
@pytest.mark.parametrize('width', [1440,390,320])
def test_student_home_focus_instrument_mode_practice_and_withdrawal(student_site, tmp_path, engine, width):
    origin, refs = student_site; errors = []; requests = []
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        ctx, page = context(browser, origin, width, errors); login(ctx, origin, refs)
        page.on('request', lambda request: requests.append((request.method,request.url)))
        before = rows(refs.factory.engine)
        page.goto('/classroom', wait_until='domcontentloaded')
        expect(page.get_by_role('heading', name='My Classroom', exact=True)).to_be_visible()
        expect(page.locator('[data-membership-status]')).to_have_text('Member')
        expect(page.locator('[data-learning-access]')).to_have_text('Learning access currently unavailable')
        expect(page.locator('[data-homework-focus]')).to_be_visible()
        assert rows(refs.factory.engine) == before
        snapshot(page, tmp_path/f'student-home-empty-{engine}-{width}.png')
        allowed_class(refs); page.reload()
        expect(page.get_by_role('link', name='Open learning tools', exact=True)).to_be_visible()
        page.get_by_label('Weekly Focus', exact=True).select_option(FOCI[1])
        page.get_by_role('button', name='Save this week’s Focus', exact=True).click()
        expect(page.locator('[data-saved-focus]')).to_have_text(FOCI[1])
        expect(page.locator('[data-issued-mode]')).to_have_text('Challenge')
        with refs.factory() as session:
            assert len(list(session.scalars(select(WeeklyPlan)))) == 1
            assert session.scalar(select(WeeklyInstrumentPlan)) is None
        page.get_by_role('button', name='Start Homework for flute', exact=True).click()
        expect(page.locator('[data-homework-slot="1"]')).to_be_visible()
        expect(page.locator('[data-homework-progress]')).to_have_text('0 / 15 minutes of matching submitted practice.')
        snapshot(page, tmp_path/f'student-home-started-{engine}-{width}.png')
        assert ctx.request.patch('/account/profile/instrument', data={'instrument':'Clarinet'},
            headers={'Origin':origin}).status == 200
        assert ctx.request.patch('/account/profile/game-mode', data={'game_mode':'Performance'},
            headers={'Origin':origin}).status == 200
        page.reload()
        expect(page.locator('[data-current-mode]')).to_have_text('Performance')
        expect(page.locator('[data-issued-mode]')).to_have_text('Challenge')
        page.get_by_role('button', name='Start Homework for clarinet', exact=True).click()
        expect(page.locator('[data-homework-slot]')).to_have_count(2)
        assert page.get_by_role('button', name='Start Homework', exact=False).count() == 0
        refs.clock[0] += timedelta(seconds=2)
        with refs.factory() as session:
            session.add(PracticeChart(profile_id=refs.profile_id, practice_date=refs.clock[0].date(),
                created_at=refs.clock[0], instrument='Clarinet', source='p-book', minutes=15,
                note='STUDENT PRIVATE NOTE', practice_details=['STUDENT PRIVATE DETAIL']))
            session.commit()
        before = rows(refs.factory.engine); page.reload()
        expect(page.locator('[data-homework-slot="2"] [data-homework-qualification]')).to_have_text('Weekly Loop qualification met')
        expect(page.locator('[data-homework-slot="1"] [data-homework-qualification]')).to_have_text('Weekly Loop qualification not yet met')
        assert 'STUDENT PRIVATE NOTE' not in page.content() and 'STUDENT PRIVATE DETAIL' not in page.content()
        assert rows(refs.factory.engine) == before
        snapshot(page, tmp_path/f'student-home-personalized-{engine}-{width}.png')
        with refs.factory() as session:
            guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
                class_id=refs.class_id, purpose='participation'); session.commit()
        page.reload()
        expect(page.locator('[data-membership-status]')).to_have_text('Member')
        expect(page.locator('[data-learning-access]')).to_have_text('Learning access currently unavailable')
        expect(page.locator('[data-homework-slot]')).to_have_count(2)
        snapshot(page, tmp_path/f'student-home-class-withdrawn-{engine}-{width}.png')
        # Hidden pages clear private content; returning rechecks all native gates.
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        expect(page.locator('[data-student-private]')).to_be_empty()
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
        expect(page.locator('[data-homework-slot]')).to_have_count(2)
        assert not any(method=='POST' and any(path in url for path in ('/claim','/reward','/classroom/reporting','/chart-sharing')) for method,url in requests)
        assert not errors
        browser.close()


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('guardian_data', [{'under13':True}], indirect=True)
@pytest.mark.parametrize('engine,width', [('chromium',1440),('firefox',390)])
def test_younger_student_own_homework_never_opens_reporting_or_unapproved_sharing(student_site, monkeypatch, tmp_path, engine, width):
    origin, refs = student_site; errors = []; allowed_class(refs)
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        ctx, page = context(browser, origin, width, errors); login(ctx, origin, refs)
        page.goto('/classroom')
        expect(page.locator('[data-learning-access]')).to_have_text('Learning access available')
        page.get_by_label('Weekly Focus', exact=True).select_option(FOCI[0])
        page.get_by_role('button', name='Save this week’s Focus', exact=True).click()
        page.get_by_role('button', name='Start Homework for flute', exact=True).click()
        expect(page.locator('[data-homework-slot]')).to_have_count(1)
        response = ctx.request.get('/classroom/reporting')
        assert 'Save authorization' not in response.text()
        choices = ctx.request.get('/classroom/chart-sharing')
        assert choices.status == 200 and choices.json()['choices'] == []
        snapshot(page, tmp_path/f'student-home-younger-{engine}-{width}.png')
        monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(False, 'open')); page.reload()
        expect(page.locator('[data-homework-unavailable]')).to_be_visible()
        assert page.locator('[data-homework-slot]').count() == 0
        with refs.factory() as session:
            child_authorization.withdraw_evidence(session, session.scalar(select(ConsentEvidence))); session.commit()
        page.reload()
        assert page.locator('[data-homework-focus], [data-homework-start], [data-homework-slot]').count() == 0
        assert 'Guardian synthetic Class' not in page.content()
        assert not errors
        browser.close()
