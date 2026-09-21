"""Minimal authenticated screening and public help; no analytics/bootstrap."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from .account_routes import current_profile
from .age_models import AccountPrivacy
from .age_privacy import declare_age,eligible,correct_age,utc
from .db import SessionLocal
from .site_admin import csrf_token,check_csrf,require_site_admin
router=APIRouter()
templates=Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent/'templates'))

def page(request,**context):
    response=templates.TemplateResponse(request=request,name='account_age.html',context=context)
    response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer'
    return response

@router.get('/account/help')
def help_page(request:Request):
    return page(request,help_only=True)

@router.get('/account/age')
def age_page(request:Request):
    with SessionLocal() as session:
        profile=current_profile(request,session)
        if profile is None:return RedirectResponse('/login',303)
        if eligible(session,profile.id):return RedirectResponse('/home',303)
        rule=session.get(AccountPrivacy,profile.id)
        return page(request,csrf=csrf_token(request),confirm_account=profile.woodchuck_id,
                    blocked_child=bool(rule and rule.age_band=='under13'),help_only=False)

@router.post('/account/age')
async def age_submit(request:Request):
    data=await request.form()
    check_csrf(request,data.get('csrf'))
    if set(data)-{'csrf','confirm_account','age_band'}:raise HTTPException(400,'Only an age band is requested.')
    with SessionLocal() as session:
        profile=current_profile(request,session)
        if profile is None:raise HTTPException(401,'Student sign-in is required.')
        if data.get('confirm_account')!=profile.woodchuck_id:raise HTTPException(409,'Sign-in changed. Reload this page.')
        try:declare_age(session,profile.id,data.get('age_band'));session.commit()
        except ValueError as error:raise HTTPException(409,str(error)) from error
        return RedirectResponse('/home' if eligible(session,profile.id) else '/account/age',303)


def support_page(request, **context):
    response = templates.TemplateResponse(request=request, name='age_support.html',
        context={'csrf': csrf_token(request), **context})
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response

@router.get('/admin/age')
def support_age_page(request: Request):
    require_site_admin(request)
    return support_page(request)

@router.post('/admin/age/lookup')
async def support_age_lookup(request: Request):
    require_site_admin(request)
    data = await request.form()
    check_csrf(request, data.get('csrf'))
    from sqlalchemy import select
    from .models import WoodchuckProfile
    with SessionLocal() as session:
        profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.woodchuck_id == data.get('woodchuck_id')))
        rule = session.get(AccountPrivacy, profile.id) if profile else None
        if not profile or profile.status != 'active' or not rule or rule.age_band not in ('under13', 'unknown'):
            raise HTTPException(409, 'No active restricted declaration available for correction.')
        return support_page(request, woodchuck_id=profile.woodchuck_id,
            previous_band=rule.age_band, expected_declared_at=utc(rule.declared_at).isoformat())

@router.post('/admin/age/correct')
async def support_age_correct(request: Request):
    require_site_admin(request)
    data = await request.form()
    check_csrf(request, data.get('csrf'))
    if data.get('support_confirmed') != 'yes':
        raise HTTPException(400, 'Confirm that support has reviewed the account holder request.')
    from sqlalchemy import select
    from .models import WoodchuckProfile
    with SessionLocal() as session:
        profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.woodchuck_id == data.get('woodchuck_id')))
        if not profile:raise HTTPException(409, 'Account not available for correction.')
        try:
            correct_age(session, profile.id, data.get('age_band'),
                expected_band=data.get('previous_band'), expected_declared_at=data.get('expected_declared_at'),
                actor=request.session['site_admin_fingerprint'], case_reference=data.get('case_reference'))
            session.commit()
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return support_page(request, completed=True)
