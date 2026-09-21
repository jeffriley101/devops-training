"""Small Free practice/parent/director pages using existing forms and metrics."""
import os,secrets
from datetime import date
from pathlib import Path
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .site_admin import csrf_token,check_csrf
from .account_routes import current_profile,SESSION_PROFILE_ID,SESSION_PROFILE_VERSION,SESSION_PAGE_GENERATION
from .age_privacy import require_eligible,eligible
from . import child_authorization as service,parent_access
from .child_models import ConsentEvidence,DirectorPermission,PendingConsent
from .models import WoodchuckProfile,PracticeChart,PracticeChartVerification,StudentVerifierConnection,TrustedVerifier
router=APIRouter(route_class=PrivateRoute)
templates=Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent/'templates'))

def page(request,kind,**context):
    return templates.TemplateResponse(request=request,name='family.html',context={'kind':kind,'csrf':csrf_token(request),**context})
def kws_label():
    try:
        from .kws_client import runtime_environment
        return 'KWS Test' if runtime_environment()=='test' else 'KWS'
    except ValueError:return 'KWS'
async def form(request,allowed):
    data=await request.form();check_csrf(request,data.get('csrf'))
    if set(data)-set(allowed)-{'csrf'} or any(len(data.getlist(k))!=1 for k in data):raise HTTPException(400,'Unexpected or duplicate form fields.')
    return data

def student(s,request,*,activity=False):
    p=current_profile(request,s)
    if not p:raise HTTPException(401,'Sign in to the existing student account.')
    if activity:require_eligible(s,p.id)
    return p

def parent(s,request):
    try:return parent_access.authenticated(s,request,lock=True)
    except ValueError:raise HTTPException(403,'Verify parent access at /family/parent-access.')

def director(s,request,*,review=False):
    try:return service.director_authenticated(s,request,review=review)
    except ValueError:raise HTTPException(403,'Verify director access at /family/director-access. Chart review needs separate permission.')

def metrics(s,p):
    from .trusted_verifier_dashboard import private_student_metrics
    from .student_practice_metrics import practice_insights
    from .feature_access import can_use_feature
    data=private_student_metrics(s,profile_id=p.id)
    if can_use_feature(s,p.id,'practice_insights'):
        charts=s.scalars(select(PracticeChart).where(PracticeChart.profile_id==p.id)).all()
        approved=set(s.scalars(select(PracticeChartVerification.practice_chart_id).join(PracticeChart).where(PracticeChart.profile_id==p.id,PracticeChartVerification.status=='approved')))
        from .contests import CENTRAL
        data['insights']=practice_insights(charts,approved,today=service.clock().astimezone(CENTRAL).date())
    return data

def charts(s,pid):
    # Deliberate private projection; no other adult's identity, tokens or unrelated records.
    rows=[]
    for c in s.scalars(select(PracticeChart).where(PracticeChart.profile_id==pid).order_by(PracticeChart.practice_date.desc())):
        rows.append({'id':c.id,'date':c.practice_date.isoformat(),'minutes':c.minutes,'note':c.note,'details':c.practice_details or []})
    return rows

@router.get('/family/notice')
def notice(request:Request):
    try:_,text,_=service.notice_policy()
    except ValueError:text=service.PRODUCTION_NOTICE if os.getenv('APP_ENV')=='production' else service.NOTICE
    return page(request,'notice',notice=text)

@router.get('/family/request')
def request_page(request:Request):
    with SessionLocal() as s:
        p=current_profile(request,s)
        return page(request,'request',available=service.under13_available(),confirm_account=p.woodchuck_id if p else 'new',kws_label=kws_label())

@router.post('/family/request')
async def request_permission(request:Request):
    data=await form(request,{'parent_email','director_email','director_name','confirm_account'})
    from .login_limits import enforce_login_limit
    enforce_login_limit(request,'student',str(data.get('parent_email','')))
    with SessionLocal() as s:
        p=current_profile(request,s)
        if data.get('confirm_account')!=(p.woodchuck_id if p else 'new'):raise HTTPException(409,'Account changed; reload the form.')
        try:service.request_consent(s,parent_email=data.get('parent_email',''),director_email=data.get('director_email',''),director_name=data.get('director_name',''),profile=p);s.commit()
        except ValueError as e:raise HTTPException(409,str(e))
    return page(request,'message',message='The parent request was sent. No new child account, membership or director invitation was created.')

@router.get('/family/approve/{token}')
def approval(request:Request,token:str):
    with SessionLocal() as s:
        try:r=service.pending_from_token(s,token)
        except ValueError as e:raise HTTPException(409,str(e))
        from .kws_models import KWSVerification
        v=s.scalar(select(KWSVerification).where(KWSVerification.pending_id==r.id))
        target=s.get(WoodchuckProfile,r.profile_id) if r.profile_id else None
        version,notice_text,_=service.notice_policy()
        return page(request,'approve',notice=notice_text,notice_version=version,kws_label=kws_label(),director_name=r.director_name,director_email=r.director_email,
                    verification_state=v.state if v else None,activation_token=service.derived_token(r,'activate') if v and v.state=='verified' else None,
                    withdrawal_token=service.derived_token(r,'withdraw'),account_label=target.woodchuck_id if target else 'one new Free under-13 child account')

@router.post('/family/approve/{token}')
async def approve(request:Request,token:str):
    data=await form(request,{'account_allowed','guardian_attestation','notice_accepted','notice_version','director_allowed','director_email','director_name','review_allowed'})
    with SessionLocal() as s:
        from . import kws_verification as kws
        from starlette.concurrency import run_in_threadpool
        try:
            started=kws.start(s,token,data)
            outcome=await run_in_threadpool(kws.deliver,s,*started) if started else None
        except ValueError as e:raise HTTPException(409,str(e))
    message=('Account permission declined. No KWS email or child account was created.' if started is None else
             'KWS accepted the verification email request. Acceptance is not verification. Complete the KWS email journey, then reopen this permission link for activation.' if outcome=='accepted' else
             'KWS delivery could not be confirmed. No verification or permission is implied. Do not repeatedly resend; reopen this permission link for status or withdraw the request.')
    return page(request,'message',message=message)

@router.get('/family/activate/{token}')
def activation_page(request:Request,token:str):
    with SessionLocal() as s:
        try:r=service.pending_from_token(s,token,purpose='activate')
        except ValueError as e:raise HTTPException(409,str(e))
        if not r.confirmed_at:raise HTTPException(409,'Confirmation pending.')
        p=current_profile(request,s)
        if r.profile_id!=(p.id if p else None):return page(request,'message',message='Sign in to the originally approved existing account, or sign out if approval was for a new account. Then reopen this link.')
        return page(request,'activate',confirm_account=p.woodchuck_id if p else 'new',existing=bool(p))

@router.post('/family/activate/{token}')
async def activate(request:Request,token:str):
    data=await form(request,{'confirm_account','display_name','pin','instrument','level','goal'})
    with SessionLocal() as s:
        p=current_profile(request,s);new=p is None
        if data.get('confirm_account')!=(p.woodchuck_id if p else 'new'):raise HTTPException(409,'Account changed.')
        try:p,e,permission=service.activate(s,token,profile=p,fields=data);s.commit()
        except ValueError as error:raise HTTPException(409,str(error))
        if new:
            request.session[SESSION_PROFILE_ID]=p.id;request.session[SESSION_PROFILE_VERSION]=p.session_version;request.session[SESSION_PAGE_GENERATION]=secrets.token_urlsafe(24)
        # Retryable link delivery after activation; never duplicate accounts or grants.
        if permission:
            try:service.send_director_link(s,permission);s.commit()
            except ValueError:s.rollback()
        return page(request,'message',message='Free private practice is ready. Keep your existing account details.'+(' Your selected director must accept and verify access; use Private practice to retry the email if needed.' if permission else ' No director sharing was authorized.'),woodchuck_id=p.woodchuck_id)

@router.get('/family/practice')
@router.get('/family/practice/data')
def practice(request:Request):
    with SessionLocal() as s:
        p=student(s,request,activity=True)
        permission=next((r for r in s.scalars(select(DirectorPermission).where(DirectorPermission.profile_id==p.id)) if service.permission_valid(s,r)),None)
        data={'charts':charts(s,p.id),'woodchuck_id':p.woodchuck_id,'metrics':metrics(s,p),'director':None}
        if permission:data['director']={'name':permission.director_name,'connected':service.permission_valid(s,permission,require_connection=True),'can_review':permission.review_allowed,'connection_id':permission.connection_id}
        if request.url.path.endswith('/data'):return data
        return page(request,'practice',**data,submission_key=secrets.token_urlsafe(20))

@router.post('/family/practice')
async def save_chart(request:Request):
    data=await form(request,{'practice_date','minutes','note','submission_key','request_review'})
    from .practice_charts import create_practice_chart_verification_request
    with SessionLocal() as s:
        p=student(s,request,activity=True);vid=None
        if data.get('request_review')=='yes':
            permission=next((r for r in s.scalars(select(DirectorPermission).where(DirectorPermission.profile_id==p.id)) if service.permission_valid(s,r,require_connection=True) and r.review_allowed),None)
            if not permission:raise HTTPException(403,'No connected director with chart-review permission.')
            vid=s.get(StudentVerifierConnection,permission.connection_id).verifier_id
        try:create_practice_chart_verification_request(s,profile=p,verifier_id=vid,practice_date=date.fromisoformat(data.get('practice_date','')),minutes=int(data.get('minutes','')),note=data.get('note',''),submission_key=data.get('submission_key'),include_contests=False,include_team_contests=False,award_dandelions=True)
        except ValueError as e:raise HTTPException(409,str(e))
    return RedirectResponse('/family/practice',303)

@router.post('/family/director-link')
async def student_director_link(request:Request):
    await form(request,set())
    with SessionLocal() as s:
        p=student(s,request,activity=True)
        permission=next((r for r in s.scalars(select(DirectorPermission).where(DirectorPermission.profile_id==p.id)) if service.permission_valid(s,r)),None)
        try:service.send_director_link(s,permission);s.commit()
        except ValueError as e:raise HTTPException(409,str(e))
    return page(request,'message',message='A verification link was sent to the authorized director.')

@router.get('/family/director-access')
def director_access_page(request:Request):return page(request,'director-access')

@router.post('/family/director-access')
async def director_access_request(request:Request):
    data=await form(request,{'woodchuck_id','email'})
    from .login_limits import enforce_login_limit
    enforce_login_limit(request,'verifier',str(data.get('email','')))
    with SessionLocal() as s:
        p=s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.woodchuck_id==data.get('woodchuck_id')))
        if p:
            permission=s.scalar(select(DirectorPermission).where(DirectorPermission.profile_id==p.id,DirectorPermission.email==str(data.get('email','')).strip().lower(),DirectorPermission.revoked_at.is_(None)).order_by(DirectorPermission.id.desc()))
            try:service.send_director_link(s,permission);s.commit()
            except ValueError:s.rollback()
    return page(request,'message',message='If current permission matches, a verification email was sent. Check your inbox.')

@router.get('/family/director-open/{token}')
def director_open_page(request:Request,token:str):return page(request,'director-open')

@router.post('/family/director-open/{token}')
async def director_open(request:Request,token:str):
    data=await form(request,{'pin','name','accept'})
    from .login_limits import enforce_login_limit
    enforce_login_limit(request,'verifier','private-director')
    with SessionLocal() as s:
        try:pid,secret=service.consume_director_link(s,token,pin=data.get('pin',''),name=data.get('name',''),accept=data.get('accept'));s.commit()
        except ValueError as e:raise HTTPException(403,str(e))
    request.session['child_director_permission']=pid;request.session['child_director_secret']=secret
    return RedirectResponse('/family/director',303)

@router.get('/family/director')
@router.get('/family/director/data')
def director_view(request:Request):
    with SessionLocal() as s:
        try:permission=director(s,request)
        except HTTPException:
            if request.url.path.endswith('/data'):raise
            response=page(request,'director-access');response.status_code=403;return response
        p=s.get(WoodchuckProfile,permission.profile_id);c=s.get(StudentVerifierConnection,permission.connection_id)
        reviews=s.execute(select(PracticeChartVerification.id,PracticeChartVerification.practice_chart_id,PracticeChartVerification.status).join(PracticeChart).where(PracticeChart.profile_id==p.id,PracticeChartVerification.verifier_id==c.verifier_id)).all()
        from .trusted_verifier_dashboard import private_student_metrics
        basic=private_student_metrics(s,profile_id=p.id)
        data={'name':p.display_name,'instrument':p.instrument,'charts':charts(s,p.id),'metrics':{key:basic[key] for key in ('weekly','lifetime','rating','trend','practice_streak')},'can_review':permission.review_allowed,'reviews':[dict(r._mapping) for r in reviews]}
        if request.url.path.endswith('/data'):return data
        return page(request,'director',**data)

@router.post('/family/director/review/{review_id}')
async def review(request:Request,review_id:int):
    data=await form(request,{'decision','note'})
    from .practice_charts import respond_to_practice_chart_verification
    with SessionLocal() as s:
        permission=director(s,request,review=True);c=s.get(StudentVerifierConnection,permission.connection_id);v=s.get(TrustedVerifier,c.verifier_id)
        try:respond_to_practice_chart_verification(s,verifier=v,verification_id=review_id,decision=data.get('decision',''),response_note=data.get('note',''),request=request)
        except (ValueError,LookupError) as e:raise HTTPException(403,str(e))
    return RedirectResponse('/family/director',303)

@router.get('/family/parent-access')
def parent_access_page(request:Request):return page(request,'parent-access',available=service.under13_available())

@router.post('/family/parent-access')
async def parent_access_request(request:Request):
    data=await form(request,{'woodchuck_id','email'})
    from .login_limits import enforce_login_limit
    enforce_login_limit(request,'verifier',str(data.get('email','')))
    with SessionLocal() as s:
        try:parent_access.request_link(s,woodchuck_id=data.get('woodchuck_id',''),email=data.get('email',''));s.commit()
        except ValueError:s.rollback()
    return page(request,'message',message='If current permission matches, a parent access email was sent. Check your inbox.')

@router.get('/family/parent-access/{token}')
def parent_link_page(request:Request,token:str):return page(request,'parent-open')

@router.post('/family/parent-access/{token}')
async def parent_link(request:Request,token:str):
    data=await form(request,{'open'})
    if data.get('open')!='yes':raise HTTPException(400,'Explicit confirmation required.')
    with SessionLocal() as s:
        try:cid,secret=parent_access.consume_link(s,token);s.commit()
        except ValueError as e:raise HTTPException(403,str(e))
    request.session['child_parent_consent']=cid;request.session['child_parent_secret']=secret
    return RedirectResponse('/family/parent',303)

@router.get('/family/parent')
@router.get('/family/parent/data')
def parent_view(request:Request):
    with SessionLocal() as s:
        try:evidence,rule,p=parent(s,request)
        except HTTPException:
            if request.url.path.endswith('/data'):raise
            response=page(request,'parent-access',available=service.under13_available());response.status_code=403;return response
        permissions=[{'id':r.id,'name':r.director_name,'email':r.email,'review_allowed':r.review_allowed} for r in s.scalars(select(DirectorPermission).where(DirectorPermission.consent_id==evidence.id,DirectorPermission.revoked_at.is_(None)))]
        data={'name':p.display_name,'metrics':metrics(s,p),'can_confirm_age':rule.age_band=='under13','permissions':permissions,'age_wording':parent_access.AGE_WORDING,'age_version':parent_access.AGE_WORDING_VERSION}
        if request.url.path.endswith('/data'):return data
        return page(request,'parent',**data)

@router.post('/family/parent/age')
async def parent_age(request:Request):
    data=await form(request,{'confirm_age','wording_version'})
    with SessionLocal() as s:
        try:parent_access.confirm_age(s,request,explicit=data.get('confirm_age'),version=data.get('wording_version'));s.commit()
        except ValueError as e:raise HTTPException(409,str(e))
    return RedirectResponse('/family/parent',303)

@router.post('/family/parent/revoke-director/{permission_id}')
async def revoke_director(request:Request,permission_id:int):
    data=await form(request,{'confirm'})
    if data.get('confirm')!='yes':raise HTTPException(400,'Confirm revocation.')
    with SessionLocal() as s:
        evidence,_,_=parent(s,request);p=s.get(DirectorPermission,permission_id)
        if not p or p.consent_id!=evidence.id:raise HTTPException(404,'Permission not found.')
        p.revoked_at=service.clock();p.link_hash=None;p.session_hash=None;s.commit()
    return RedirectResponse('/family/parent',303)

@router.get('/family/withdraw/{token}')
def withdrawal_page(request:Request,token:str):return page(request,'withdraw')

@router.post('/family/withdraw/{token}')
async def withdraw(request:Request,token:str):
    data=await form(request,{'withdraw'})
    if data.get('withdraw')!='yes':raise HTTPException(400,'Confirm withdrawal.')
    from .security import hash_invitation_token
    with SessionLocal() as s:
        # Match activation's lock order. Query evidence only after the pending row
        # is locked, so an activation finishing while we wait is also withdrawn.
        try:r=s.scalar(select(PendingConsent).where(PendingConsent.id==int(token.split('.',1)[0])).with_for_update().execution_options(populate_existing=True))
        except ValueError:r=None
        import hmac
        if not r or not hmac.compare_digest(service.derived_token(r,'withdraw').encode(),token.encode()):raise HTTPException(403,'Invalid withdrawal link.')
        e=s.scalar(select(ConsentEvidence).where(ConsentEvidence.withdrawal_hash==hash_invitation_token(token)).with_for_update())
        if e:service.withdraw_evidence(s,e)
        else:
            from .kws_verification import cancel
            cancel(s,r)
        s.commit()
    return page(request,'message',message='Permission withdrawn. Account activity and adult access are disabled. Saved data and memberships were not deleted or changed; contact support for privacy requests.')

@router.post('/family/parent/withdraw')
async def parent_withdraw(request:Request):
    data=await form(request,{'withdraw'})
    if data.get('withdraw')!='yes':raise HTTPException(400,'Confirm withdrawal.')
    with SessionLocal() as s:e,_,_=parent(s,request);service.withdraw_evidence(s,e);s.commit()
    request.session.pop('child_parent_secret',None)
    return page(request,'message',message='Permission withdrawn. Saved data and entitlements remain intact; account activity is paused.')


@router.post('/family/logout')
async def close_adult(request:Request):
    await form(request,set())
    from .child_models import ParentAccess
    with SessionLocal() as s:
        cid=request.session.get('child_parent_consent');pid=request.session.get('child_director_permission')
        for cls,key in ((ParentAccess,cid),(DirectorPermission,pid)):
            row=s.get(cls,key) if type(key) is int else None
            if row:
                from .security import hash_invitation_token
                keyname='child_parent_secret' if cls is ParentAccess else 'child_director_secret'
                secret=request.session.get(keyname)
                if isinstance(secret,str) and row.session_hash==hash_invitation_token(secret):
                    row.session_hash=None;row.session_expires_at=None
        s.commit()
    for key in ('child_parent_consent','child_parent_secret','child_director_permission','child_director_secret'):
        request.session.pop(key,None)
    return RedirectResponse('/family/parent-access',303)

@router.post('/family/disconnect')
async def disconnect(request:Request):
    data=await form(request,{'connection_id','confirm'})
    if data.get('confirm')!='yes':raise HTTPException(400,'Confirm disconnection.')
    with SessionLocal() as s:
        p=student(s,request)
        try:cid=int(data.get('connection_id',''))
        except ValueError:raise HTTPException(400,'Invalid connection.')
        c=s.get(StudentVerifierConnection,cid)
        if not c or c.profile_id!=p.id:raise HTTPException(404,'Connection not found.')
        # Serialize with private review before retiring the shared connection.
        for consent_id in s.scalars(select(DirectorPermission.consent_id).where(DirectorPermission.connection_id==cid).order_by(DirectorPermission.consent_id)):
            s.scalar(select(ConsentEvidence).where(ConsentEvidence.id==consent_id).with_for_update())
        c.status='disconnected'
        for permission in s.scalars(select(DirectorPermission).where(DirectorPermission.connection_id==cid)):
            permission.revoked_at=service.clock();permission.link_hash=None;permission.session_hash=None
        s.commit()
    return RedirectResponse('/family/practice',303)
