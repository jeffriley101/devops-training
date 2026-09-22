"""Standalone Free child authorization with an optional durable tester claim.

A claim grants nothing before successful activation. No deletion or
provider-suitability approval is implied.
"""
from datetime import datetime, timedelta, timezone
import hashlib, hmac, html, os, re
from sqlalchemy import select, func
from .age_models import AccountPrivacy
from .child_models import PendingConsent, ConsentEvidence, DirectorPermission
from .security import generate_invitation_token, hash_invitation_token
from .email_service import EmailService, build_message, public_link
from .session_config import session_secret

UNDER13_REVIEW_APPROVED = False
NOTICE_VERSION = 'private-practice-kws-test-v3'
NOTICE = '''DRAFT — Parent permission for private Woodshed practice
Your child may use Free private practice: save practice charts and revisit their history. Full membership is optional and controls existing Insights; permission does not buy or grant access.
After authorization we store their chosen name, account ID, hashed PIN, instrument, level, goal, private practice dates/durations/details/notes, progress, game results, rewards and account activity. Guest microphone processing is local. Guest history is not imported. Under-13 individual results remain excluded from public and other-student standings.
Account permission covers Free private practice. Director sharing is optional and separately selectable: declining it does not prevent Free private practice. Only if selected, you authorize the specific named director to see your child's identity, instrument, practice charts, dates, durations, details/notes and basic practice metrics. The director must accept the connection and verify private access. If you separately check chart review, that director may approve/reject submitted charts and return a review note. Approval is per connection; it does not make results public. You can withdraw director sharing without ending private practice, or withdraw account permission. Your child may disconnect the director.
You receive private parent access through an expiring emailed link. It covers only your child and their membership-appropriate metrics. Matching an email, paying for membership or holding a verifier PIN does not substitute for authorization. After your explicit declaration that your child is 13 or older, future qualifying activity may become public; earlier private history remains private. Existing parent/director permissions retain their scope and withdrawal controls. Your student may then establish new ordinary director connections without new parent permission; those new directors receive only qualifying practice after the transition, not earlier private charts or cumulative history. The transition day is excluded because practice dates have day-level precision.
Render hosts the application/database. Configured Gmail SMTP processes recipient addresses, message contents and private links; consumer Gmail suitability remains under review. No Workspace protections or fixed provider deletion guarantee is claimed.
This isolated Test integration uses Epic Kids Web Services (KWS) to verify an adult. KWS receives the parent email, configured location/language and an opaque request reference. Card verification occurs with KWS; Woodshed does not receive card details. Adult verification alone does not establish guardianship or consent: you must separately attest that you are this child's parent/legal guardian and explicitly accept this notice and selected permissions. Verification does not log you in as a parent, require Full, or grant every permission. Provider suitability, policy and retention review remain outstanding; production activation is closed.
Proposed timings: unverified requests expire after 48 hours; activation expires 72 hours after verification. Signed results have a local 24-hour freshness limit with five minutes of clock skew. These are tested technical settings, not approved retention periods. This candidate does not run deletion/anonymization or retention cleanup. A reviewed retention/deletion procedure and scheduler are required before production activation.
Contact Woodshed Woodchuck LLC. Phone (314) 514-5611; email woodshedwoodchuck@gmail.com. Request access, correction, withdrawal or deletion through support. This draft and the authorization method require review before launch.'''
NOTICE_SHA256 = hashlib.sha256(NOTICE.encode()).hexdigest()
PRODUCTION_NOTICE_VERSION = 'private-practice-kws-production-v3'
PRODUCTION_NOTICE = """Parent permission for private Woodshed practice
Your child may use Free private practice: save practice charts and revisit their history. Full membership is optional and controls existing Insights; permission does not buy or grant access.
After authorization we store their chosen name, account ID, hashed PIN, instrument, level, goal, private practice dates/durations/details/notes, progress, game results, rewards and account activity. Guest microphone processing is local. Guest history is not imported. Under-13 individual results remain excluded from public and other-student standings.
Account permission covers Free private practice. Director sharing is optional and separately selectable: declining it does not prevent Free private practice. Only if selected, you authorize the specific named director to see your child's identity, instrument, practice charts, dates, durations, details/notes and basic practice metrics. The director must accept the connection and verify private access. If you separately check chart review, that director may approve/reject submitted charts and return a review note. Approval is per connection; it does not make results public. You can withdraw director sharing without ending private practice, or withdraw account permission. Your child may disconnect the director.
You receive private parent access through an expiring emailed link. It covers only your child and their membership-appropriate metrics. Matching an email, paying for membership or holding a verifier PIN does not substitute for authorization. After your explicit declaration that your child is 13 or older, future qualifying activity may become public; earlier private history remains private. Existing parent/director permissions retain their scope and withdrawal controls. Your student may then establish new ordinary director connections without new parent permission; those new directors receive only qualifying practice after the transition, not earlier private charts or cumulative history. The transition day is excluded because practice dates have day-level precision.
Render hosts the application and database. Configured Gmail SMTP processes recipient addresses, message contents and private links used for Woodshed email delivery.
Woodshed uses Epic Kids Web Services (KWS) to verify an adult. KWS receives the parent email, configured location/language and an opaque request reference. Card verification occurs with KWS; Woodshed does not receive card details. Adult verification alone does not establish guardianship or consent: you must separately attest that you are this child's parent or legal guardian and explicitly accept this notice and selected permissions. Verification does not log you in as a parent, require Full membership or grant every permission.
Unverified requests expire after 48 hours. The Woodshed activation link expires 72 hours after verification. Signed verification results have a local 24-hour freshness limit with five minutes of clock skew.
Contact Woodshed Woodchuck LLC. Phone (314) 514-5611; email woodshedwoodchuck@gmail.com. Request access, correction, withdrawal or deletion through support."""
PRODUCTION_NOTICE_SHA256 = hashlib.sha256(PRODUCTION_NOTICE.encode()).hexdigest()
PENDING_TTL=timedelta(hours=48)
CONFIRMATION_DELAY=timedelta(hours=24)
APPROVED_TTL=timedelta(hours=72)
ACCESS_TTL=timedelta(minutes=30)
REQUEST_INTERVAL=timedelta(minutes=5)

def clock():return datetime.now(timezone.utc)
def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
def notice_policy(environment=None):
    if environment is None:
        from .kws_client import runtime_environment
        environment=runtime_environment()
    if environment=='test':return NOTICE_VERSION,NOTICE,NOTICE_SHA256
    if environment=='production':return PRODUCTION_NOTICE_VERSION,PRODUCTION_NOTICE,PRODUCTION_NOTICE_SHA256
    raise ValueError('KWS environment is unavailable.')
def under13_available():
    from .kws_client import Config,KWSUnavailable,runtime_environment
    try:
        environment=runtime_environment()
        Config.load(environment)
        return True
    except KWSUnavailable:
        return bool(UNDER13_REVIEW_APPROVED and os.getenv('APP_ENV')=='kws-test')
def require_under13_review():
    if not under13_available():raise ValueError('Parent authorization is not open yet. Guest tools and help remain available.')
def consent_active(session,rule):
    e=session.get(ConsentEvidence,rule.consent_id,populate_existing=True) if rule and rule.consent_id else None
    if not under13_available() or not e:return False
    try:version,_,digest=notice_policy()
    except (ValueError, RuntimeError):return False
    return bool(e.profile_id==rule.profile_id and e.parent_email and e.approved_at and e.confirmed_at
                and not e.withdrawn_at and e.notice_version==version and e.notice_sha256==digest)
def protected_child(session,pid):
    rule=session.get(AccountPrivacy,pid,populate_existing=True)
    return bool(rule and (rule.age_band=='under13' or rule.consent_id))
def send_copy(service,recipient,subject,text):
    if service.config is None:raise ValueError('Email delivery is not configured.')
    result=service.send(build_message(to_email=recipient,subject=subject,plain_text=text,html_body='<pre>'+html.escape(text)+'</pre>',config=service.config))
    if not result.sent:raise ValueError('Email could not be delivered; no authorization is implied.')
def derived_token(row,purpose):
    value=f'{purpose}:{row.id}:{utc(row.created_at).isoformat()}'
    return str(row.id)+'.'+hmac.new(session_secret().encode(),value.encode(),hashlib.sha256).hexdigest()
def director_copy(row):return f'\nSuggested director (optional): {row.director_name} <{row.director_email}>. Sharing and chart review require separate parent selection.' if row.director_email else '\nNo director sharing is requested. Free private practice does not require it.'

def request_consent(session,*,parent_email,director_email='',director_name='',profile=None,
                    cohort_key=None):
    require_under13_review()
    version,notice,_=notice_policy()
    from .verifiers import validate_email
    from .age_privacy import declare_age
    email=validate_email(parent_email);director=validate_email(director_email) if director_email else ''
    name=director_name.strip()
    if director and (not 1<=len(name)<=80 or re.search(r'[\x00-\x1f\x7f]',name)):raise ValueError('Enter the director name, without control characters.')
    if not director:name=''
    now=clock()
    if cohort_key is not None:
        from .tester_enrollments import C001,normalize_cohort_key
        cohort_key=normalize_cohort_key(cohort_key)
        if cohort_key!=C001 or profile is not None:
            raise ValueError('Tester enrollment is available only for a new C001 account.')
        cohort_claimed_at=now
    else:
        cohort_claimed_at=None
    if profile:
        if profile.status!='active':raise ValueError('Account unavailable.')
        rule=session.get(AccountPrivacy,profile.id)
        if rule and consent_active(session,rule):raise ValueError('Current account permission already exists.')
        declare_age(session,profile.id,'under13')
    if session.scalar(select(PendingConsent.id).where(PendingConsent.parent_email==email,PendingConsent.expires_at>now)):
        raise ValueError('An unexpired request exists. Check the parent inbox.')
    if session.scalar(select(func.count(PendingConsent.id)).where(PendingConsent.created_at>now-timedelta(hours=1)))>=125:
        raise ValueError('Permission requests are temporarily busy.')
    token=generate_invitation_token()
    row=PendingConsent(profile_id=profile.id if profile else None,parent_email=email,director_email=director,director_name=name,review_allowed=False,approve_hash=hash_invitation_token(token),created_at=now,expires_at=now+PENDING_TTL,notice_version=version,cohort_key=cohort_key,cohort_claimed_at=cohort_claimed_at)
    session.add(row);session.flush()
    send_copy(EmailService(),email,'Woodshed: parent permission request',notice+director_copy(row)+'\nRead and choose permissions: '+public_link('/family/approve/'+token)+'\nWithdraw this request: '+public_link('/family/withdraw/'+derived_token(row,'withdraw')))
    return row

def pending_from_token(session,token,*,purpose='approve',lock=False):
    if not isinstance(token,str) or not 20<=len(token)<=160:raise ValueError('Invalid or expired link.')
    column=PendingConsent.approve_hash if purpose=='approve' else PendingConsent.activation_hash
    q=select(PendingConsent).where(column==hash_invitation_token(token))
    row=session.scalar((q.with_for_update() if lock else q).execution_options(populate_existing=True))
    version,_,_=notice_policy()
    if not row or utc(row.expires_at)<=clock() or row.notice_version!=version:raise ValueError('Invalid, expired or superseded notice link.')
    return row

def approve(session,token,*,explicit,notice_version,review_allowed):
    raise ValueError('The prototype email-only approval is disabled. Use the KWS permission flow.')

def send_due_confirmations(session):
    require_under13_review()
    # KWS results replace the email-plus timer; the old CLI cannot bypass verification.
    return 0

def activate(session,token,*,profile,fields):
    require_under13_review()
    from .models import WoodchuckProfile,WoodchuckState
    from .accounts import create_woodchuck_profile
    from .economy import preserve_server_values
    from .age_privacy import declare_age
    row=pending_from_token(session,token,purpose='activate',lock=True)
    from .kws_verification import verified_for_activation
    verification=verified_for_activation(session,row)
    if not row.confirmed_at or row.profile_id!=(profile.id if profile else None):raise ValueError('Confirm the originally authorized account; authorization is not student authentication.')
    if session.scalar(select(ConsentEvidence.id).where(ConsentEvidence.activation_hash==hash_invitation_token(token))):raise ValueError('Activation already used. Sign in to your existing account.')
    if profile:
        profile=session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==profile.id).with_for_update().execution_options(populate_existing=True))
        if profile.status!='active':raise ValueError('Account unavailable.')
        rule=session.get(AccountPrivacy,profile.id,populate_existing=True)
        if rule and consent_active(session,rule):raise ValueError('Permission already active.')
    else:
        name=str(fields.get('display_name','')).strip()
        if not 1<=len(name)<=40 or re.search(r'[\x00-\x1f\x7f]|@|https?://|\d{7,}',name):raise ValueError('Choose a short name without contact information.')
        for key in ('instrument','level','goal'):
            value=str(fields.get(key,''))
            if not 1<=len(value)<=100 or re.search(r'[\x00-\x1f\x7f]',value):raise ValueError('Complete account details.')
        profile=create_woodchuck_profile(session,display_name=name,pin=fields.get('pin',''),instrument=fields['instrument'],level=fields['level'],goal=fields['goal'],commit=False)
        state=preserve_server_values({});state['profile']={'woodchuckName':profile.display_name,'instrument':profile.instrument,'level':profile.level,'goal':profile.goal}
        state['account']={'woodchuckId':profile.woodchuck_id,'authenticated':True,'serverRevision':0}
        session.add(WoodchuckState(profile_id=profile.id,state_json=state,revision=0))
        if row.cohort_key:
            from .tester_enrollments import enroll_tester
            if not row.cohort_claimed_at:raise ValueError('Incomplete tester cohort claim.')
            enroll_tester(session,profile.id,row.cohort_key,row.cohort_claimed_at)
    rule=declare_age(session,profile.id,'under13')
    e=ConsentEvidence(profile_id=profile.id,parent_email=row.parent_email,activation_hash=hash_invitation_token(token),withdrawal_hash=hash_invitation_token(derived_token(row,'withdraw')),notice_version=row.notice_version,notice_sha256=verification.notice_sha256,approved_at=row.approved_at,confirmed_at=row.confirmed_at)
    session.add(e);session.flush();rule.consent_id=e.id
    permission=None
    if verification.director_allowed:
        permission=DirectorPermission(profile_id=profile.id,consent_id=e.id,email=row.director_email,director_name=row.director_name,review_allowed=row.review_allowed,authorized_at=clock())
        session.add(permission);session.flush()
    verification.state='activated'
    verification.activated_consent_id=e.id
    # No director mail before the parent-authorized account transaction is complete.
    row.expires_at=clock();session.flush()
    return profile,e,permission

def permission_valid(session,p,*,require_connection=False):
    from .models import WoodchuckProfile,StudentVerifierConnection,TrustedVerifier
    if not p or p.revoked_at:return False
    rule=session.get(AccountPrivacy,p.profile_id,populate_existing=True)
    profile=session.get(WoodchuckProfile,p.profile_id)
    if not profile or profile.status!='active' or not rule or rule.consent_id!=p.consent_id or not consent_active(session,rule):return False
    if p.connection_id:
        c=session.get(StudentVerifierConnection,p.connection_id,populate_existing=True)
        v=session.get(TrustedVerifier,c.verifier_id) if c else None
        return bool(c and c.status=='accepted' and c.profile_id==p.profile_id and c.role=='band_director' and v and v.email.casefold()==p.email.casefold())
    return not require_connection

def send_director_link(session,p):
    if not permission_valid(session,p):raise ValueError('Director permission is unavailable or disconnected.')
    now=clock()
    if p.requested_at and utc(p.requested_at)+REQUEST_INTERVAL>now:raise ValueError('Wait five minutes before requesting another link.')
    token=generate_invitation_token();p.link_hash=hash_invitation_token(token);p.link_expires_at=now+ACCESS_TTL;p.requested_at=now
    send_copy(EmailService(),p.email,'Woodshed: verify private director access','A parent authorized a private practice connection. Verify your email and accept the connection if new. Your verifier PIN alone cannot open private student data. Link expires in 30 minutes: '+public_link('/family/director-open/'+token))
    session.flush()

def consume_director_link(session,token,*,pin,name,accept):
    from .models import TrustedVerifier,StudentVerifierConnection,WoodchuckProfile,TrustedVerifierInvitation
    from .security import verify_pin
    from .verifiers import create_trusted_verifier_invitation,accept_trusted_verifier_invitation,reissue_trusted_verifier_invitation
    p=session.scalar(select(DirectorPermission).where(DirectorPermission.link_hash==hash_invitation_token(token)).with_for_update().execution_options(populate_existing=True))
    if not permission_valid(session,p) or not p.link_expires_at or utc(p.link_expires_at)<=clock():raise ValueError('Verify director access using a current emailed link.')
    v=session.scalar(select(TrustedVerifier).where(TrustedVerifier.email==p.email))
    existing=session.scalar(select(StudentVerifierConnection).where(StudentVerifierConnection.profile_id==p.profile_id,StudentVerifierConnection.verifier_id==v.id,StudentVerifierConnection.status=='accepted',StudentVerifierConnection.role=='band_director')) if v else None
    if existing:
        if not verify_pin(pin,v.pin_hash):raise ValueError('Use the existing verifier PIN with this emailed link.')
        p.connection_id=existing.id
    else:
        if p.connection_id:raise ValueError('The old connection is unavailable. Fresh parent permission is required.')
        if accept!='yes':raise ValueError('Explicit acceptance is required for a new director connection.')
        profile=session.get(WoodchuckProfile,p.profile_id)
        pending=session.scalar(select(TrustedVerifierInvitation).where(TrustedVerifierInvitation.profile_id==p.profile_id,TrustedVerifierInvitation.email==p.email,TrustedVerifierInvitation.role=='band_director',TrustedVerifierInvitation.status=='pending').with_for_update())
        if pending:
            created=reissue_trusted_verifier_invitation(session,profile=profile,invitation_id=pending.id,commit=False)
        else:
            created=create_trusted_verifier_invitation(session,profile=profile,email=p.email,role='band_director',commit=False)
        accepted=accept_trusted_verifier_invitation(session,token=created.token,display_name=name,pin=pin,commit=False,private_permission=p)
        p.connection_id=accepted.connection.id
    secret=generate_invitation_token();p.link_hash=None;p.link_expires_at=None;p.session_hash=hash_invitation_token(secret);p.session_expires_at=clock()+ACCESS_TTL
    session.flush();return p.id,secret

def director_authenticated(session,request,*,review=False):
    pid=request.session.get('child_director_permission');secret=request.session.get('child_director_secret')
    if type(pid) is not int or not isinstance(secret,str):raise ValueError('Verify director access using the emailed link.')
    p=session.get(DirectorPermission,pid,populate_existing=True)
    if p:
        session.scalar(select(ConsentEvidence).where(ConsentEvidence.id==p.consent_id).with_for_update().execution_options(populate_existing=True))
        p=session.scalar(select(DirectorPermission).where(DirectorPermission.id==pid).with_for_update().execution_options(populate_existing=True))
        if p.connection_id:
            from .models import StudentVerifierConnection
            session.scalar(select(StudentVerifierConnection).where(StudentVerifierConnection.id==p.connection_id).with_for_update().execution_options(populate_existing=True))
    if not permission_valid(session,p,require_connection=True) or not p.session_hash or not p.session_expires_at or utc(p.session_expires_at)<=clock() or not hmac.compare_digest(p.session_hash,hash_invitation_token(secret)):
        raise ValueError('Verify director access using a current emailed link.')
    if review and not p.review_allowed:raise ValueError('This connection permits viewing, not chart approval.')
    return p

def permitted_director(session,profile_id,verifier_id,*,review=False):
    from .models import StudentVerifierConnection
    for p in session.scalars(select(DirectorPermission).where(DirectorPermission.profile_id==profile_id,DirectorPermission.revoked_at.is_(None))):
        if permission_valid(session,p,require_connection=True):
            c=session.get(StudentVerifierConnection,p.connection_id)
            if c.verifier_id==verifier_id and (not review or p.review_allowed):return p
    return None

def withdraw_evidence(session,evidence):
    from .models import WoodchuckProfile
    if not evidence.withdrawn_at:
        evidence.withdrawn_at=clock()
        profile=session.get(WoodchuckProfile,evidence.profile_id)
        if profile and profile.status=='active':profile.session_version+=1
    session.flush();return evidence

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['send-confirmations']);parser.parse_args()
    from .db import SessionLocal
    with SessionLocal() as s:
        print({'confirmations_sent':send_due_confirmations(s)});s.commit()
