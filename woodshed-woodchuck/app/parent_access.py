"""Narrow, consent-bound parent access. Email equality alone never authorizes a read."""
import hmac
from datetime import timedelta
from sqlalchemy import select
from . import child_authorization as service
from .age_models import AccountPrivacy
from .child_models import ConsentEvidence, ParentAccess, ParentAgeDeclaration
from .child_authorization import consent_active
from .models import WoodchuckProfile
from .security import generate_invitation_token, hash_invitation_token

ACCESS_TTL=timedelta(minutes=30)
REQUEST_INTERVAL=timedelta(minutes=5)
AGE_WORDING_VERSION='parent-13-declaration-v2'
AGE_WORDING='My child is now 13 or older.'
AGE_EXPLANATION=('Future qualifying activity may appear in leaderboards. Earlier private history stays private. '
    'Practice recorded for today or earlier remains private; later practice dates may qualify. '
    'This is your declaration, not independent age verification. Your existing private access and withdrawal controls remain in place.')

def valid_consent(session, consent_id, *, lock=False):
    q=select(ConsentEvidence).where(ConsentEvidence.id==consent_id)
    evidence=session.scalar((q.with_for_update() if lock else q).execution_options(populate_existing=True))
    rule=session.get(AccountPrivacy,evidence.profile_id,populate_existing=True) if evidence else None
    profile=session.get(WoodchuckProfile,evidence.profile_id,populate_existing=True) if evidence else None
    if not evidence or not rule or rule.consent_id!=evidence.id or not profile or profile.status!='active' or not consent_active(session,rule):
        raise ValueError('Parent access is unavailable. Request a current access link or contact support.')
    return evidence,rule,profile

def request_link(session, *, woodchuck_id, email):
    service.require_under13_review()
    # Generic response for unknown inputs; bounded credential row, no rejection audit rows.
    if not isinstance(woodchuck_id,str) or not isinstance(email,str) or len(woodchuck_id)>32 or len(email)>254:return
    profile=session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.woodchuck_id==woodchuck_id.strip()))
    rule=session.get(AccountPrivacy,profile.id) if profile else None
    if not rule or not rule.consent_id:return
    try:evidence,_,_=valid_consent(session,rule.consent_id,lock=True)
    except ValueError:return
    if not hmac.compare_digest(evidence.parent_email.casefold(),email.strip().casefold()):return
    access=session.get(ParentAccess,evidence.id);now=service.clock()
    if access and access.requested_at and service.utc(access.requested_at)+REQUEST_INTERVAL>now:return
    if not access:access=ParentAccess(consent_id=evidence.id);session.add(access)
    token=generate_invitation_token();access.link_hash=hash_invitation_token(token)
    access.link_expires_at=now+ACCESS_TTL;access.requested_at=now;session.flush()
    service.send_copy(service.EmailService(),evidence.parent_email,'Woodshed: private parent access',
        'Open your private parent view using this one-use link within 30 minutes. Do not forward it. '
        'It does not sign in to the student account or grant chart approval.\n'+
        service.public_link('/family/parent-access/'+token))

def consume_link(session,token):
    if not isinstance(token,str) or not 20<=len(token)<=160:raise ValueError('Invalid or expired parent access link.')
    # Consent -> access lock order shared with withdrawal/age changes.
    access=session.scalar(select(ParentAccess).where(ParentAccess.link_hash==hash_invitation_token(token)))
    if not access:raise ValueError('Invalid or expired parent access link.')
    evidence,_,_=valid_consent(session,access.consent_id,lock=True)
    access=session.scalar(select(ParentAccess).where(ParentAccess.consent_id==evidence.id).with_for_update().execution_options(populate_existing=True))
    if access.link_hash!=hash_invitation_token(token) or not access.link_expires_at or service.utc(access.link_expires_at)<=service.clock():
        raise ValueError('Invalid or expired parent access link.')
    secret=generate_invitation_token();access.link_hash=None;access.link_expires_at=None
    access.session_hash=hash_invitation_token(secret);access.session_expires_at=service.clock()+ACCESS_TTL
    session.flush();return evidence.id,secret

def authenticated(session,request,*,lock=False):
    cid=request.session.get('child_parent_consent');secret=request.session.get('child_parent_secret')
    if type(cid) is not int or not isinstance(secret,str):raise ValueError('Verify parent access using the emailed link.')
    evidence,rule,profile=valid_consent(session,cid,lock=lock)
    access=session.get(ParentAccess,cid,populate_existing=True)
    if not access or not access.session_hash or not access.session_expires_at or service.utc(access.session_expires_at)<=service.clock() or not hmac.compare_digest(access.session_hash,hash_invitation_token(secret)):
        raise ValueError('Parent access expired. Request a new link.')
    return evidence,rule,profile

def parent_verifier(session,profile_id,verifier_id):
    rule=session.get(AccountPrivacy,profile_id)
    if not rule or not rule.consent_id:return False
    try:evidence,_,_=valid_consent(session,rule.consent_id)
    except ValueError:return False
    access=session.get(ParentAccess,evidence.id)
    return bool(access and access.verifier_id==verifier_id)

def invitation_allowed(session,profile_id,email,role):
    rule=session.get(AccountPrivacy,profile_id)
    if not rule or not rule.consent_id or role!='verifier':return False
    try:evidence,_,_=valid_consent(session,rule.consent_id)
    except ValueError:return False
    return email.strip().casefold()==evidence.parent_email.casefold()

def confirm_age(session,request,*,explicit,version):
    evidence,rule,profile=authenticated(session,request,lock=True)
    if explicit!='yes' or version!=AGE_WORDING_VERSION:raise ValueError('Check the confirmation and explicitly save it.')
    if rule.age_band!='under13' or session.get(ParentAgeDeclaration,profile.id):raise ValueError('This age confirmation is already recorded or unavailable.')
    # No birth date exists in the current schema. Fail closed if a later integration supplies one.
    if getattr(profile,'birth_date',None) is not None or getattr(profile,'date_of_birth',None) is not None:
        raise ValueError('A recorded birth date needs support review before this age declaration can be saved.')
    now=service.clock()
    rule.age_band='13to17';rule.declared_at=now;rule.public_from=now
    rule.private_plunge_best=profile.plunge_best_score or 0
    session.add(ParentAgeDeclaration(profile_id=profile.id,consent_id=evidence.id,confirmed_at=now,wording_version=version))
    session.flush();return profile
