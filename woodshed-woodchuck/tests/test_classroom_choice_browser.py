"""Real PostgreSQL-backed native director and student browsers."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select
from app import classroom_s2 as s2, classroom_directors_choice as choice
from app import classroom_guardian_authorization as guardian
from app.classroom_choice_models import ClassroomChoiceVersion as Version
from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan
from test_classroom_directors_choice import (choice_data, guardian_data, migrated_db, captured, adult_pin_hash,
    actor, student, parent_accept, parent_proof, saved, CUSTOM, PATH, rows)
from test_classroom_student_home import student_home, issue, start
from test_classroom_s2 import create, code
from test_classroom_guardian_browser import guardian_site, context, snapshot, parent_login
from test_classroom_enrollment_ui_browser import launch


@pytest.fixture
def choice_site(guardian_site, choice_data, student_home):
    origin,refs=guardian_site
    assert refs is choice_data and refs is student_home
    issue(refs);start(refs)
    with refs.factory() as session:
        room=create(session,name='Second synthetic Class');session.commit()
        code(session,room.id,preferred='CHOICE22');session.commit()
        role=s2.assign_teacher(session,actor=actor(session),program_id=1,class_id=room.id,verifier_id=2);session.commit()
        member=s2.join_class(session,student=student(session),program_id=1,code='CHOICE22');session.commit()
        other=SimpleNamespace(**(vars(refs)|{'class_id':room.id,'membership_id':member.id,'assignment_id':role.id}))
        parent_accept(session,other,'participation');session.commit()
    saved(other,fields=CUSTOM|{'title':'Second Class rhythm'})
    return origin,refs


def send(page,form,button):
    form.get_by_label('Director PIN',exact=True).fill('2468')
    with page.expect_response(lambda r:r.request.method=='POST' and '/classroom/directors-choice' in r.url) as result:
        form.get_by_role('button',name=button,exact=True).click()
    assert result.value.status==200,result.value.text()


@pytest.mark.parametrize('migrated_db',['postgresql'],indirect=True)
@pytest.mark.parametrize('engine',['chromium','firefox'])
@pytest.mark.parametrize('width',[1440,390,320])
def test_create_edit_recipe_cancel_history_and_separate_student_work(choice_site,tmp_path,engine,width):
    origin,refs=choice_site;errors=[]
    with sync_playwright() as playwright:
        browser=launch(playwright,engine)
        ctx,page=context(browser,origin,width,errors)
        assert ctx.request.post('/trusted-verifiers/login',form={'email':'adult2@example.test','pin':'2468'},headers={'Origin':origin}).status==200
        page.goto(PATH,wait_until='domcontentloaded')
        send(page,page.locator('form'),'Open assignments')
        expect(page.get_by_role('heading',name='Your assigned Classes')).to_be_visible()
        page.get_by_role('link',name='Manage assignments').first.click()
        send(page,page.locator('form'),'Open assignments')
        page.get_by_text('Short practice instructions',exact=True).click()
        form=page.locator('[data-choice-form="custom-new"]')
        form.get_by_label('Practice title',exact=True).fill('Long tones <script>window.bad=true</script>')
        form.get_by_label('Short music-practice instructions',exact=True).fill('Play two measures slowly.\nListen for steady tone.')
        send(page,form,'Assign practice instructions')
        card=page.locator('#choice-list .choice-item').first
        expect(card).to_contain_text('Long tones <script>window.bad=true</script>')
        assert page.evaluate('window.bad') is None
        first_id=card.locator('[data-choice-id]').get_attribute('data-choice-id')
        card.get_by_text('Edit assignment',exact=True).click()
        form=page.locator(f'[data-choice-form="custom-{first_id}"]')
        form.get_by_label('Practice title',exact=True).fill('Steady tone revision')
        send(page,form,'Save new version')
        card=page.locator('#choice-list .choice-item').first
        card.get_by_text('Version history',exact=True).click()
        send(page,card.locator(f'[data-history-id="{first_id}"]'),'Show version history')
        expect(page.locator('#choice-history')).to_contain_text('Long tones <script>window.bad=true</script>')
        expect(page.locator('#choice-history')).to_contain_text('Steady tone revision')
        snapshot(page,tmp_path/f'director-history-{engine}-{width}.png')
        page.get_by_text('Existing Homework recipe',exact=True).click()
        form=page.locator('[data-choice-form="recipe-new"]')
        form.get_by_label('Recipe instrument').select_option('flute')
        form.get_by_label('Recipe Focus').select_option('Improve tone and control')
        form.get_by_label('Recipe Game Mode').select_option('Challenge')
        send(page,form,'Assign recipe')
        recipe=page.locator('#choice-list .choice-item').first
        expect(recipe).to_contain_text('Steady tone and control')
        recipe.get_by_text('Cancel assignment',exact=True).click()
        send(page,recipe.locator('[data-cancel-id]'),'Confirm cancellation')
        expect(page.locator('#choice-list .choice-item').first).to_contain_text('Cancelled · Read-only')
        snapshot(page,tmp_path/f'director-cancelled-{engine}-{width}.png')
        child_ctx,child=context(browser,origin,width,errors)
        assert child_ctx.request.post('/account/login',form={'woodchuck_id':refs.woodchuck_id,'pin':'2468'},headers={'Origin':origin}).status==200
        before=rows(refs.factory.engine)
        child.goto('/classroom',wait_until='domcontentloaded')
        expect(child.locator('#student-directors-choice [data-choice-class]')).to_have_count(2)
        expect(child.locator('#student-directors-choice')).to_contain_text('Steady tone revision')
        expect(child.locator('#student-directors-choice')).to_contain_text('Second Class rhythm')
        expect(child.locator('#student-homework')).not_to_contain_text('Steady tone revision')
        expect(child.locator('#student-homework [data-homework-slot]')).to_have_count(1)
        snapshot(child,tmp_path/f'student-separate-{engine}-{width}.png')
        child.locator(f'[data-choice-class="{refs.class_id}"]').get_by_role('link',name='Open Class assignments and weeks').click()
        expect(child.locator('#choice-list')).to_contain_text('Steady tone revision')
        assert child.get_by_text('Edit assignment',exact=True).count()==0
        assert rows(refs.factory.engine)==before
        refs.clock[0]=choice.bounds(choice.week())[1]
        child.reload()
        child.get_by_label('Week starting Monday').fill((choice.week()-timedelta(weeks=1)).isoformat())
        child.get_by_role('button',name='Show week',exact=True).click()
        expect(child.locator('#choice-list')).to_contain_text('Expired · Read-only')
        parent_ctx,parent=context(browser,origin,width,errors)
        parent_login(parent,refs)
        section=parent.locator(f'[data-parent-class="{refs.class_id}"] [data-parent-purpose=participation]')
        section.get_by_label('Withdraw this permission for this Class.').check()
        section.get_by_role('button',name='Withdraw participation',exact=True).click()
        response=child.reload(wait_until='domcontentloaded')
        assert response.status==403 and 'Steady tone revision' not in child.content()
        assert errors==[]
        parent_ctx.close();child_ctx.close();ctx.close();browser.close()
