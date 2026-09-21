"""Standalone child Free practice; captured email and disposable PostgreSQL only."""
import re,json,hmac,hashlib
from datetime import datetime,timedelta,timezone,date
from types import SimpleNamespace
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select,func
from test_age_screening import age_db,login,state,counts,screen
from app.main import app
from app import child_authorization as service,parent_access
from app.child_models import PendingConsent,ConsentEvidence,DirectorPermission,ParentAccess
from app.age_models import AccountPrivacy
from app.models import PracticeChart,PracticeChartVerification,Membership,StudentVerifierConnection,TrustedVerifier,WoodchuckProfile
from app.email_service import SMTPConfig,DeliveryResult,EmailService

@pytest.fixture
def captured(monkeypatch):
    mail=[];now=[datetime.now(timezone.utc)]
    class Capture(EmailService):
        def __init__(self):pass
        config=SMTPConfig('capture.invalid',587,'synthetic','synthetic','operator@example.test','Woodshed',True)
        def send(self,message):mail.append(message);return DeliveryResult(True,'captured')
    monkeypatch.setattr(service,'EmailService',Capture)
    from app import verifier_routes
    monkeypatch.setattr(verifier_routes,'EmailService',Capture)
    monkeypatch.setattr(service,'UNDER13_REVIEW_APPROVED',True)
    monkeypatch.setattr(service,'clock',lambda:now[0])
    monkeypatch.setenv('PUBLIC_BASE_URL','https://testserver')
    for name,value in {'APP_ENV':'kws-test','KWS_TEST_ENABLED':'true','KWS_ENVIRONMENT':'test',
        'KWS_TEST_DATABASE_CONFIRMED':'true','KWS_TEST_CLIENT_ID':'synthetic-client',
        'KWS_TEST_API_KEY':'synthetic-api-key','KWS_TEST_ORG_ID':'synthetic-org',
        'KWS_TEST_LOCATION_JSON':'"US"',
        'KWS_TEST_RECIPIENTS_JSON':'["synthetic@example.test"]',
        'KWS_TEST_WEBHOOK_SECRETS':'["synthetic-webhook-secret"]',
        'KWS_TEST_VERIFICATION_SECRETS':'["synthetic-redirect-secret"]'}.items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('KWS_TEST_PRODUCT_ID',raising=False)
    from app import kws_verification as kws
    requests=[];monkeypatch.setattr(kws,'captured_requests',requests,raising=False)
    def send(cfg,email,payload):requests.append((email,payload));return True
    monkeypatch.setattr(kws.client,'send_email',send)
    return mail,now

def path(mail,kind):
    body=next(m for m in reversed(mail) if '/family/'+kind+'/' in m.get_body(preferencelist=('plain',)).get_content()).get_body(preferencelist=('plain',)).get_content()
    return re.search(r'/family/'+kind+r'/[^\s]+',body).group()

def post(c,url,values={},page=None):
    r=c.get(page or url)
    assert r.status_code==200,r.text
    return c.post(url,data={'csrf':r.context['csrf'],**values},follow_redirects=False)

def authorize(factory,captured,*,existing=True,review=True,tag='a',sharing=True):
    mail,now=captured;c=TestClient(app)
    if existing:login(c)
    saved=state(factory)
    with factory() as s:
        prior_connections=s.scalar(select(func.count(StudentVerifierConnection.id)))
        prior_memberships=s.scalar(select(func.count(Membership.id)))
    assert post(c,'/family/request',{'parent_email':f'parent-{tag}@example.test','director_email':f'director-{tag}@example.test','director_name':'Synthetic Director','confirm_account':'WC-AGE-A' if existing else 'new'}).status_code==200
    assert len(mail)==1 and mail[0]['To']==f'parent-{tag}@example.test'
    with factory() as s:assert s.scalar(select(func.count(StudentVerifierConnection.id)))==prior_connections
    parent=TestClient(app);approve=path(mail,'approve')
    fields={'account_allowed':'yes','guardian_attestation':'yes','notice_accepted':'yes','notice_version':service.NOTICE_VERSION}
    if sharing:fields.update(director_allowed='yes',director_email=f'director-{tag}@example.test',director_name='Synthetic Director')
    if review and sharing:fields['review_allowed']='yes'
    r=post(parent,approve,fields);assert r.status_code==200,r.text
    assert post(parent,approve,fields).status_code==409
    with factory() as s:assert service.send_due_confirmations(s)==0
    from app import kws_verification as kws
    email,payload=kws.captured_requests[-1]
    raw=json.dumps({'name':'parent-verified','orgId':'synthetic-org','productId':None,
        'payload':{'parentEmail':email,'externalPayload':payload,'status':{'verified':True,'transactionId':'synthetic-'+tag}}}).encode()
    timestamp=str(int(now[0].timestamp()));signature=hmac.new(b'synthetic-webhook-secret',timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    r=parent.post('/family/kws/test/webhook',content=raw,headers={'x-kws-signature':f't={timestamp},v1={signature}'});assert r.status_code==200,r.text
    assert parent.get('/family/parent/data').status_code==403  # Verification is not parent login.
    activation='/family/activate/'+parent.get(approve).context['activation_token']
    values={'confirm_account':'WC-AGE-A'} if existing else {'confirm_account':'new','display_name':'Synthetic Child','pin':'2468','instrument':'Flute','level':'Beginner','goal':'Practice every day'}
    r=post(c,activation,values);assert r.status_code==200,r.text
    assert c.get('/account/state').status_code==200
    with factory() as s:
        assert s.scalar(select(func.count(Membership.id)))==prior_memberships
        p=s.scalar(select(WoodchuckProfile).join(AccountPrivacy).where(AccountPrivacy.consent_id.is_not(None)))
        pid=p.id;wid=p.woodchuck_id
    if existing:assert state(factory)==saved
    assert len(mail)==(2 if sharing else 1)
    if sharing:assert mail[-1]['To']==f'director-{tag}@example.test'
    return c,parent,pid,wid

def connect(mail,*,accept=True):
    d=TestClient(app)
    r=post(d,path(mail,'director-open'),{'pin':'1357','name':'Synthetic Director',**({'accept':'yes'} if accept else {})})
    assert r.status_code==303,r.text
    assert d.get('/family/director/data').status_code==200
    return d

def open_parent(parent,captured,wid,email='parent-a@example.test'):
    mail,now=captured
    assert post(parent,'/family/parent-access',{'woodchuck_id':wid,'email':email}).status_code==200
    r=post(parent,path(mail,'parent-access'),{'open':'yes'});assert r.status_code==303,r.text
    return parent

def save(c,*,review=True,note='private synthetic note'):
    r=c.get('/family/practice');assert r.status_code==200,r.text
    return c.post('/family/practice',data={'csrf':r.context['csrf'],'submission_key':r.context['submission_key'],'practice_date':str(date.today()),'minutes':15,'note':note,**({'request_review':'yes'} if review else {})},follow_redirects=False)

@pytest.mark.parametrize('existing',[True,False])
def test_complete_free_private_journey(age_db,captured,existing):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured,existing=existing)
    assert save(c,review=False).status_code==303
    assert len(c.get('/family/practice/data').json()['charts'])==1
    d=connect(mail)
    assert save(c).status_code==303
    data=d.get('/family/director/data').json();assert len(data['charts'])==2
    assert set(data['metrics'])=={'weekly','lifetime','rating','trend','practice_streak'}
    rid=data['reviews'][0]['id']
    r=post(d,f'/family/director/review/{rid}',{'decision':'approved','note':'Synthetic approval'},page='/family/director');assert r.status_code==303,r.text
    p=open_parent(p,captured,wid)
    info=p.get('/family/parent/data').json();assert 'insights' not in info['metrics']
    assert 'private synthetic note' in d.get('/family/director').text
    with age_db() as s:
        assert s.get(PracticeChartVerification,rid).status=='approved'
        assert s.scalar(select(func.count(Membership.id)))==0
        assert all(not x.include_contests for x in s.scalars(select(PracticeChart).where(PracticeChart.profile_id==pid)))
    assert c.get('/c001').status_code==404
    assert 'cohorts' not in counts(age_db)


def test_pin_only_wrong_adults_old_links_and_revocation(age_db,captured):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail);assert save(c).status_code==303
    rid=d.get('/family/director/data').json()['reviews'][0]['id']
    outsider=TestClient(app)
    # A genuine ordinary verifier PIN login still cannot authorize private child reads/actions.
    from app.verifier_routes import SESSION_VERIFIER_ID
    from itsdangerous import TimestampSigner
    from base64 import b64encode
    import json
    from app.main import SESSION_SECRET
    with age_db() as s:vid=s.scalar(select(TrustedVerifier.id))
    outsider.cookies.set('session',TimestampSigner(SESSION_SECRET).sign(b64encode(json.dumps({SESSION_VERIFIER_ID:vid}).encode())).decode())
    assert outsider.get('/family/director/data').status_code==403
    assert outsider.get('/family/parent/data').status_code==403
    from app.practice_charts import respond_to_practice_chart_verification
    with age_db() as s:
        with pytest.raises(ValueError):respond_to_practice_chart_verification(s,verifier=s.get(TrustedVerifier,vid),verification_id=rid,decision='approved',response_note='')
    p=open_parent(p,captured,wid)
    with age_db() as s:permission=s.scalar(select(DirectorPermission));permission_id=permission.id
    assert post(p,f'/family/parent/revoke-director/{permission_id+50}',{'confirm':'yes'},page='/family/parent').status_code==404
    assert post(p,f'/family/parent/revoke-director/{permission_id}',{'confirm':'yes'},page='/family/parent').status_code==303
    assert d.get('/family/director/data').status_code==403
    assert post(d,f'/family/director/review/{rid}',{'decision':'approved'},page='/family/director-access').status_code==403
    assert c.get('/family/practice/data').status_code==200
    with age_db() as s:assert s.get(PracticeChartVerification,rid).status=='pending'


def test_parent_session_expiry_age_and_withdrawal_preserve_data(age_db,captured):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail);assert save(c).status_code==303
    p=open_parent(p,captured,wid);oldchart=c.get('/family/practice/data').json()['charts'][0]['id']
    assert post(p,'/family/parent/age',{'wording_version':parent_access.AGE_WORDING_VERSION},page='/family/parent').status_code==409
    assert post(p,'/family/parent/age',{'wording_version':parent_access.AGE_WORDING_VERSION,'confirm_age':'yes'},page='/family/parent').status_code==303
    with age_db() as s:
        from app.age_privacy import chart_public
        assert s.get(AccountPrivacy,pid).age_band=='13to17'
        assert not chart_public(s,s.get(PracticeChart,oldchart))
    assert d.get('/family/director/data').status_code==200
    assert post(p,'/family/parent/age',{'wording_version':parent_access.AGE_WORDING_VERSION,'confirm_age':'yes'},page='/family/parent').status_code==409
    now[0]+=timedelta(minutes=31)
    for adult,url in [(d,'director'),(p,'parent')]:
        assert adult.get('/family/'+url+'/data').status_code==403
        expired=adult.get('/family/'+url)
        assert expired.status_code==403 and 'Send verification link' in expired.text and 'Synthetic A' not in expired.text
    p=open_parent(p,captured,wid);before=state(age_db)
    assert post(p,'/family/parent/withdraw',{'withdraw':'yes'},page='/family/parent').status_code==200
    assert c.get('/account/state').status_code in (401,403)
    assert p.get('/family/parent/data').status_code==403
    assert state(age_db)==before
    with age_db() as s:assert s.get(PracticeChart,oldchart) and s.get(WoodchuckProfile,pid).status=='active'


def test_gate_and_expired_forged_approval_no_records(age_db,captured,monkeypatch):
    mail,now=captured;c=TestClient(app);before=counts(age_db)
    monkeypatch.setattr(service,'UNDER13_REVIEW_APPROVED',False)
    monkeypatch.setenv('KWS_TEST_ENABLED','false')
    assert 'name="parent_email"' not in c.get('/family/request').text
    assert post(c,'/family/request',{'parent_email':'parent@example.test','director_email':'d@example.test','director_name':'Director','confirm_account':'new'}).status_code==409
    assert not mail and counts(age_db)==before
    monkeypatch.setattr(service,'UNDER13_REVIEW_APPROVED',True)
    monkeypatch.setenv('KWS_TEST_ENABLED','true')
    assert post(c,'/family/request',{'parent_email':'parent@example.test','director_email':'d@example.test','director_name':'Director','confirm_account':'new'}).status_code==200
    assert c.get('/family/approve/'+'x'*40).status_code==409
    now[0]+=timedelta(hours=49)
    assert c.get(path(mail,'approve')).status_code==409
    with age_db() as s:assert s.scalar(select(func.count(ConsentEvidence.id)))==0


def test_existing_accepted_connection_reused_and_disconnect(age_db,captured):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail)
    with age_db() as s:cid=s.scalar(select(StudentVerifierConnection.id))
    now[0]+=timedelta(minutes=6)
    assert post(c,'/family/director-link',page='/family/practice').status_code==200
    d=connect(mail,accept=False)
    with age_db() as s:assert list(s.scalars(select(StudentVerifierConnection.id)))==[cid]
    other=TestClient(app);login(other,'B')
    assert post(other,'/family/disconnect',{'confirm':'yes','connection_id':cid},page='/family/request').status_code==404
    assert post(c,'/family/disconnect',{'confirm':'yes','connection_id':cid},page='/family/practice').status_code==303
    assert d.get('/family/director/data').status_code==403
    assert c.get('/family/practice/data').status_code==200


def test_view_permission_is_not_chart_review(age_db,captured):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured,review=False);d=connect(mail)
    assert save(c,review=False).status_code==303
    assert save(c,review=True).status_code==403
    assert d.get('/family/director/data').json()['can_review'] is False
    assert post(d,'/family/director/review/1',{'decision':'approved'},page='/family/director').status_code==403

@pytest.mark.parametrize('source',['manual','stripe','paypal'])
def test_membership_controls_parent_insights_without_changing_access(age_db,captured,source):
    from app.models import BillingAccount,MembershipSeat
    from app.memberships import student_has_full_access
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);p=open_parent(p,captured,wid)
    assert 'insights' not in p.get('/family/parent/data').json()['metrics']
    with age_db() as s:
        b=BillingAccount(profile_id=pid);s.add(b);s.flush()
        m=Membership(billing_account_id=b.id,source=source,status='active');s.add(m);s.flush()
        s.add(MembershipSeat(membership_id=m.id,profile_id=pid,slot_number=1));s.commit();mid=m.id
        assert student_has_full_access(s,pid)
    assert 'insights' in p.get('/family/parent/data').json()['metrics']
    assert save(c,review=False).status_code==303
    with age_db() as s:
        m=s.get(Membership,mid);m.status='ended';s.commit()
    assert 'insights' not in p.get('/family/parent/data').json()['metrics']
    assert c.get('/family/practice').status_code==200


def test_13plus_connection_stays_ordinary_and_child_generic_sharing_denied(age_db,captured):
    from app.verifiers import create_trusted_verifier_invitation,accept_trusted_verifier_invitation,band_director_students
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail)
    assert c.post('/trusted-verifiers/invitations',data={'email':'stranger@example.test','role':'band_director'}).status_code==403
    assert c.post('/teams',json={'name':'Private child team','emblem_key':'emoji:bear'}).status_code==403
    with age_db() as s:
        vid=s.scalar(select(TrustedVerifier.id));assert band_director_students(s,verifier_id=vid)==[]
    older=TestClient(app);login(older,'B');assert screen(older,'13to17').status_code==303
    with age_db() as s:
        invite=create_trusted_verifier_invitation(s,profile=s.get(WoodchuckProfile,2),email='director-a@example.test',role='band_director')
        accepted=accept_trusted_verifier_invitation(s,token=invite.token,display_name='Synthetic Director',pin='1357')
        assert [r['profile_id'] for r in band_director_students(s,verifier_id=accepted.verifier.id)]==[2]
        assert s.get(AccountPrivacy,2).consent_id is None


def test_wrong_parent_cannot_gain_other_child_access_or_change_scope(age_db,captured):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);p=open_parent(p,captured,wid)
    before=len(mail)
    assert post(p,'/family/parent-access',{'woodchuck_id':'WC-AGE-B','email':'parent-a@example.test'}).status_code==200
    assert len(mail)==before
    # Even a verified parent cannot select another subject or add permission via submitted IDs.
    assert post(p,'/family/parent/age',{'confirm_age':'yes','wording_version':parent_access.AGE_WORDING_VERSION,'profile_id':2},page='/family/parent').status_code==400
    assert p.get('/family/parent/data?profile_id=2').json()['name']=='Synthetic A'
    assert 'Synthetic B' not in p.get('/family/parent').text
    assert post(p,path(mail,'parent-access'),{'open':'yes'}).status_code==403


def test_consent_notice_change_closes_sessions_and_director_links(age_db,captured,monkeypatch):
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail);p=open_parent(p,captured,wid)
    monkeypatch.setattr(service,'NOTICE_SHA256','f'*64)
    assert c.get('/account/state').status_code==403
    assert p.get('/family/parent/data').status_code==403
    assert d.get('/family/director/data').status_code==403


def test_13plus_http_connection_invitation_needs_no_parent(age_db,captured):
    mail,now=captured;c=TestClient(app);login(c);assert screen(c,'13to17').status_code==303
    r=c.post('/trusted-verifiers/invitations',data={'email':'older-director@example.test','role':'band_director'})
    assert r.status_code==200,r.text
    assert mail[-1]['To']=='older-director@example.test'
    body=mail[-1].get_body(preferencelist=('plain',)).get_content()
    assert 'Synthetic A' in body
    with age_db() as s:assert s.scalar(select(func.count(PendingConsent.id)))==0


def test_pending_parent_request_sends_no_child_invitation_before_confirmation(age_db,captured):
    mail,now=captured;c=TestClient(app);login(c)
    assert post(c,'/family/request',{'parent_email':'parent-c@example.test','director_email':'director-c@example.test','director_name':'Synthetic Director','confirm_account':'WC-AGE-A'}).status_code==200
    assert all(m['To']=='parent-c@example.test' for m in mail)
    assert 'Synthetic A' not in mail[0].get_body(preferencelist=('plain',)).get_content()
    assert c.post('/trusted-verifiers/invitations',data={'email':'director-c@example.test','role':'band_director'}).status_code==403
    assert c.get('/account/state').status_code==403


def test_sensitive_family_links_are_not_logged(age_db,captured,caplog):
    mail,now=captured;c=TestClient(app)
    assert post(c,'/family/request',{'parent_email':'log-parent@example.test','director_email':'director@example.test','director_name':'Director','confirm_account':'new'}).status_code==200
    url=path(mail,'approve');c.get(url)
    assert url.rsplit('/',1)[1] not in caplog.text
    assert 'log-parent@example.test' not in caplog.text


def test_preexisting_director_pending_chart_and_membership_are_preserved(age_db,captured):
    from app.models import BillingAccount,MembershipSeat
    from app.security import hash_pin
    with age_db() as s:
        v=TrustedVerifier(email='director-a@example.test',display_name='Existing Director',pin_hash=hash_pin('1357'));s.add(v);s.flush()
        connection=StudentVerifierConnection(profile_id=1,verifier_id=v.id,role='band_director',status='accepted');s.add(connection);s.flush();cid=connection.id
        chart=PracticeChart(profile_id=1,practice_date=date(2026,9,1),minutes=22,instrument='Flute',note='Private retained chart',include_contests=False,include_team_contests=False);s.add(chart);s.flush();chartid=chart.id
        pending=PracticeChartVerification(practice_chart_id=chart.id,verifier_id=v.id,status='pending');s.add(pending);s.flush();rid=pending.id
        b=BillingAccount(profile_id=1);s.add(b);s.flush();m=Membership(billing_account_id=b.id,source='manual',status='active');s.add(m);s.flush();mid=m.id
        s.add(MembershipSeat(membership_id=m.id,profile_id=1,slot_number=1));s.commit()
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail,accept=False)
    assert 'Private retained chart' in d.get('/family/director').text
    assert post(d,f'/family/director/review/{rid}',{'decision':'approved'},page='/family/director').status_code==303
    with age_db() as s:
        assert list(s.scalars(select(StudentVerifierConnection.id)))==[cid]
        assert s.get(PracticeChart,chartid).note=='Private retained chart'
        assert s.get(Membership,mid).status=='active'
        assert s.scalar(select(DirectorPermission)).connection_id==cid


def test_old_generic_invite_cannot_reopen_private_director_after_native_disconnect(age_db,captured):
    from app.models import TrustedVerifierInvitation
    from app.security import hash_invitation_token
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured);d=connect(mail)
    with age_db() as s:
        cid=s.scalar(select(StudentVerifierConnection.id))
        old=TrustedVerifierInvitation(profile_id=pid,email='director-a@example.test',role='band_director',token_hash=hash_invitation_token('synthetic-old-invite-token'),status='pending',expires_at=datetime.now(timezone.utc)+timedelta(days=1));s.add(old);s.commit()
    assert c.delete(f'/trusted-verifiers/connections/{cid}').status_code==200
    assert d.get('/family/director/data').status_code==403
    r=d.post('/trusted-verifiers/invitations/synthetic-old-invite-token/accept',data={'display_name':'Director','pin':'1357'})
    assert r.status_code==400,r.text
    with age_db() as s:
        assert s.get(StudentVerifierConnection,cid).status=='disconnected'
        assert s.scalar(select(DirectorPermission)).revoked_at is not None


def test_matching_pending_director_invite_reused_only_through_parent_authorized_link(age_db,captured):
    from app.models import TrustedVerifierInvitation
    from app.security import hash_invitation_token
    with age_db() as s:
        old=TrustedVerifierInvitation(profile_id=1,email='director-a@example.test',role='band_director',token_hash=hash_invitation_token('old-private-token'),status='pending',expires_at=datetime.now(timezone.utc)+timedelta(days=1));s.add(old);s.commit();oldid=old.id
    mail,now=captured;c,p,pid,wid=authorize(age_db,captured)
    stranger=TestClient(app)
    assert stranger.post('/trusted-verifiers/invitations/old-private-token/accept',data={'display_name':'Director','pin':'1357'}).status_code==400
    d=connect(mail)
    with age_db() as s:
        assert list(s.scalars(select(TrustedVerifierInvitation.id)))==[oldid]
        assert s.get(TrustedVerifierInvitation,oldid).status=='accepted'
    assert save(c).status_code==303
