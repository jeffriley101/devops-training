"""Transition scoping and independent recovery withdrawal, synthetic PostgreSQL."""
import sys,re,json,importlib.util
from pathlib import Path
from datetime import datetime,timedelta,timezone,date
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app,SESSION_SECRET
from app.session_revocations import RevocableSessionMiddleware
from app.models import PracticeChart,PracticeChartVerification,StudentVerifierConnection,TrustedVerifier,Membership,WoodchuckProfile
from app.child_models import ConsentEvidence,DirectorPermission
from app.age_models import AccountPrivacy
from app import child_authorization as service,parent_access
from app.age_privacy import ordinary_director_connection,director_chart_visible
from test_age_screening import age_db,state,counts
from test_private_practice import captured,authorize,connect,open_parent,post,path,save


def load_recovery():
    # Import only the independent boundary/router, not the recovery app or seeds.
    root=Path('/recovery/app')
    if not root.exists():root=Path(__file__).resolve().parents[3]/'recovery/woodshed-woodchuck/app'
    modules=[]
    for name in ('recovery_authorization','age_recovery'):
        spec=importlib.util.spec_from_file_location('app.'+name,root/(name+'.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);modules.append(module)
    routes,boundary=modules
    recovery=FastAPI();recovery.include_router(routes.router)
    recovery.add_middleware(RevocableSessionMiddleware,secret_key=SESSION_SECRET,same_site='lax',https_only=False)
    recovery.add_middleware(boundary.AgeRecoveryBoundary)
    return recovery,routes,boundary

@pytest.fixture
def recovery(age_db,captured,monkeypatch):
    app,routes,boundary=load_recovery();monkeypatch.setattr(routes,'SessionLocal',age_db);monkeypatch.setattr(routes,'clock',service.clock)
    return app,routes,boundary


def transitioned(factory,captured):
    mail,now=captured;now[0]=datetime.now(timezone.utc)-timedelta(days=2)
    c,p,pid,wid=authorize(factory,captured);d=connect(mail)
    assert save(c,review=False,note='EARLIER PRIVATE HISTORY').status_code==303
    with factory() as s:
        old=s.scalar(select(PracticeChart));old.practice_date=now[0].date()-timedelta(days=2);old.created_at=now[0]-timedelta(days=2);s.commit();oldid=old.id
        connection=s.scalar(select(StudentVerifierConnection));connection.invited_at=now[0]-timedelta(seconds=10);s.commit()
    p=open_parent(p,captured,wid)
    r=post(p,'/family/parent/age',{'confirm_age':'yes','wording_version':parent_access.AGE_WORDING_VERSION},page='/family/parent');assert r.status_code==303
    assert d.get('/family/director/data').status_code==200
    assert 'EARLIER PRIVATE HISTORY' in d.get('/family/director').text
    assert p.get('/family/parent/data').json()['metrics']['lifetime']['total']==15
    return c,p,d,pid,wid,oldid


def new_director(factory,captured,c):
    mail,now=captured
    with factory() as s:oldcid=s.scalar(select(StudentVerifierConnection.id))
    assert c.delete(f'/trusted-verifiers/connections/{oldcid}').status_code==200
    r=c.post('/trusted-verifiers/invitations',data={'email':'new-director@example.test','role':'band_director'});assert r.status_code==200,r.text
    token=r.json()['invitation_token'];d=TestClient(app)
    r=d.post('/trusted-verifiers/invitations/'+token+'/accept',data={'display_name':'New Director','pin':'9876'});assert r.status_code==200,r.text
    with factory() as s:
        v=s.scalar(select(TrustedVerifier).where(TrustedVerifier.email=='new-director@example.test'));vid=v.id
    return d,vid


def test_transition_new_ordinary_director_sees_only_eligible_new_records(age_db,captured):
    c,p,oldadult,pid,wid,oldid=transitioned(age_db,captured);before=state(age_db)
    d,vid=new_director(age_db,captured,c)
    assert oldadult.get('/family/director/data').status_code==403  # explicit student disconnection
    assert p.get('/family/parent/data').json()['metrics']['lifetime']['total']==15
    r=c.post('/practice-charts',json={'practice_date':str(date.today()),'minutes':9,'note':'NEW ELIGIBLE PRACTICE','verifier_id':vid,'submission_key':'new-transition-practice','include_contests':True,'include_team_contests':False})
    assert r.status_code==201,r.text
    with age_db() as s:
        new=s.scalar(select(PracticeChart).where(PracticeChart.note=='NEW ELIGIBLE PRACTICE'));newid=new.id
        assert director_chart_visible(s,new,vid)
        assert not director_chart_visible(s,s.get(PracticeChart,oldid),vid)
        # Old pending/review links that happen to target this verifier are still denied.
        oldreview=PracticeChartVerification(practice_chart_id=oldid,verifier_id=vid,status='pending');s.add(oldreview);s.commit();rid=oldreview.id
    pending=d.get('/trusted-verifiers/practice-charts').json()['pending_charts']
    assert [x['chart']['id'] for x in pending]==[newid]
    with age_db() as s:
        newrid=s.scalar(select(PracticeChartVerification.id).where(PracticeChartVerification.practice_chart_id==newid))
    assert d.post(f'/trusted-verifiers/practice-charts/{newrid}/respond',json={'decision':'approved'}).status_code==200
    r=d.post(f'/trusted-verifiers/practice-charts/{rid}/respond',json={'decision':'approved'});assert r.status_code==400
    page=d.get('/band-director/dashboard');assert page.status_code==200,page.text
    assert page.context['students'][0]['lifetime']['total']==9
    csv=d.get('/band-director/dashboard.csv');assert csv.status_code==200
    from app.band_director_practice import band_director_practice_students
    with age_db() as s:
        summary=band_director_practice_students(s,verifier_id=vid)
        assert summary[0]['this_week_minutes']==9
        assert len(summary[0]['recent_charts'])==1
        assert summary[0]['team'] is None and summary[0]['contest'] is None
        assert s.get(PracticeChart,oldid).note=='EARLIER PRIVATE HISTORY'
    # Backdated new submissions cannot bypass the day boundary.
    r=c.post('/practice-charts',json={'practice_date':'2026-01-01','minutes':8,'note':'Backdated','verifier_id':vid,'submission_key':'backdated-private'});assert r.status_code==400
    assert c.post('/trusted-verifiers/invitations',data={'email':'other-adult@example.test','role':'verifier'}).status_code==400
    assert c.post('/teams',json={'name':'Not broadened','emblem_key':'emoji:bear'}).status_code==403
    assert state(age_db)[1]['practiceLog']==before[1]['practiceLog']
    with age_db() as s:assert s.scalar(select(func.count(Membership.id)))==0


def test_existing_director_does_not_gain_generic_history_after_transition(age_db,captured):
    c,p,d,pid,wid,oldid=transitioned(age_db,captured)
    with age_db() as s:
        vid=s.scalar(select(TrustedVerifier.id));assert ordinary_director_connection(s,pid,vid) is None
    assert d.get('/family/director/data').status_code==200
    assert p.get('/family/parent/data').status_code==200


def recovery_client(recovery,original):
    c=TestClient(recovery[0]);c.cookies.update(original.cookies);return c

def rpost(c,url,field):
    r=c.get(url);assert r.status_code==200,r.text
    csrf=re.search(r'name="csrf" value="([^"]+)"',r.text).group(1)
    return c.post(url,data={'csrf':csrf,field:'yes'},follow_redirects=False)


def test_recovery_parent_withdrawal_remains_effective_after_repair(age_db,captured,recovery):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail);assert save(c).status_code==303;p=open_parent(p,captured,wid)
    saved=state(age_db);rc=recovery_client(recovery,p)
    assert rc.get('/family/parent/data').status_code==503
    assert rc.get('/family/director').status_code==503
    assert rpost(rc,'/family/parent/withdraw','withdraw').status_code==200
    assert p.get('/family/parent/data').status_code==403
    assert d.get('/family/director/data').status_code==403
    assert c.get('/account/state').status_code in (401,403)
    assert state(age_db)==saved
    with age_db() as s:
        assert s.scalar(select(ConsentEvidence)).withdrawn_at
        assert s.scalar(select(PracticeChartVerification)).status=='pending'
        assert s.get(WoodchuckProfile,pid).status=='active'


def test_recovery_revokes_director_without_ending_private_practice(age_db,captured,recovery):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail);p=open_parent(p,captured,wid)
    with age_db() as s:permissionid=s.scalar(select(DirectorPermission.id))
    rc=recovery_client(recovery,p)
    assert rpost(rc,f'/family/parent/revoke-director/{permissionid}','confirm').status_code==200
    assert d.get('/family/director/data').status_code==403
    assert c.get('/family/practice').status_code==200
    assert p.get('/family/parent/data').status_code==200


def test_recovery_email_token_withdrawal_replay_and_unrelated_adult_denial(age_db,captured,recovery):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail)
    anonymous=TestClient(recovery[0]);before=counts(age_db)
    assert rpost(anonymous,'/family/parent/withdraw','withdraw').status_code==403
    assert rpost(anonymous,'/family/withdraw/'+'x'*40,'withdraw').status_code==403
    assert counts(age_db)==before
    url=path(mail,'withdraw')
    page=anonymous.get(url);csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    assert anonymous.post(url,data={'csrf':'forged','withdraw':'yes'}).status_code==403
    assert anonymous.post(url,data={'csrf':csrf,'withdraw':'yes'},headers={'Origin':'https://wrong.invalid'}).status_code==403
    assert rpost(anonymous,url,'withdraw').status_code==200
    with age_db() as s:stamp=s.scalar(select(ConsentEvidence)).withdrawn_at;version=s.get(WoodchuckProfile,pid).session_version
    assert rpost(anonymous,url,'withdraw').status_code==200
    with age_db() as s:
        assert s.scalar(select(ConsentEvidence)).withdrawn_at==stamp and s.get(WoodchuckProfile,pid).session_version==version
    assert d.get('/family/director/data').status_code==403


def test_recovery_expired_parent_cannot_revoke_but_withdrawal_link_works(age_db,captured,recovery):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);p=open_parent(p,captured,wid)
    now[0]+=timedelta(minutes=31);rc=recovery_client(recovery,p)
    assert rpost(rc,'/family/parent/withdraw','withdraw').status_code==403
    assert rpost(rc,path(mail,'withdraw'),'withdraw').status_code==200
    assert p.get('/family/parent/data').status_code==403


def test_recovery_wrong_parent_director_revocation_scope_and_pending_cancel(age_db,captured,recovery):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);p=open_parent(p,captured,wid)
    rc=recovery_client(recovery,p)
    with age_db() as s:
        # Different child's permission cannot be selected by an authorized parent.
        e=ConsentEvidence(profile_id=2,parent_email='other@example.test',notice_version=service.NOTICE_VERSION,notice_sha256=service.NOTICE_SHA256,approved_at=now[0],confirmed_at=now[0]);s.add(e);s.flush()
        perm=DirectorPermission(profile_id=2,consent_id=e.id,email='other-director@example.test',director_name='Other',review_allowed=True,authorized_at=now[0]);s.add(perm);s.commit();otherid=perm.id
    assert rpost(rc,f'/family/parent/revoke-director/{otherid}','confirm').status_code==404
    with age_db() as s:assert s.get(DirectorPermission,otherid).revoked_at is None


def test_recovery_cancels_pending_request_without_activation(age_db,captured,recovery):
    mail,now=captured
    from app.child_models import PendingConsent
    with age_db() as s:
        row=service.request_consent(s,parent_email='pending@example.test',director_email='pending-director@example.test',director_name='Pending Director')
        token=service.derived_token(row,'withdraw');rid=row.id;s.commit()
    rc=TestClient(recovery[0]);assert rpost(rc,'/family/withdraw/'+token,'withdraw').status_code==200
    with age_db() as s:
        assert service.utc(s.get(PendingConsent,rid).expires_at)<=now[0]
        assert s.scalar(select(func.count(ConsentEvidence.id)))==0
        assert s.scalar(select(func.count(DirectorPermission.id)))==0
    assert len(mail)==1  # recovery never sends mail


def test_ordinary_13plus_director_existing_behavior_is_preserved(age_db,captured):
    from test_age_screening import login
    from app.age_privacy import declare_age
    from app.verifiers import create_trusted_verifier_invitation,accept_trusted_verifier_invitation
    with age_db() as s:
        profile=s.get(WoodchuckProfile,1);declare_age(s,1,'adult')
        invitation=create_trusted_verifier_invitation(s,profile=profile,email='ordinary@example.test',role='band_director')
        accepted=accept_trusted_verifier_invitation(s,token=invitation.token,display_name='Ordinary Director',pin='9876');vid=accepted.verifier.id
        chart=PracticeChart(profile_id=1,practice_date=date(2025,1,1),minutes=19,instrument='Flute',note='Existing ordinary history',source='p-book',credits_awarded=0,created_at=datetime(2025,1,1,tzinfo=timezone.utc));s.add(chart);s.flush();s.add(PracticeChartVerification(practice_chart_id=chart.id,verifier_id=vid,status='pending'));s.commit()
        assert director_chart_visible(s,chart,vid)
        from app.band_director_dashboard import dashboard_metrics
        metrics=dashboard_metrics(s,verifier_id=vid);assert metrics['students'][0]['lifetime']['total']==19
