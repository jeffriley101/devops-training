"""Read-only presentation status for an existing under-13 student's latest flow."""
from sqlalchemy import select
from . import child_authorization as service
from .age_models import AccountPrivacy
from .age_privacy import utc
from .child_models import ConsentEvidence, PendingConsent
from .kws_models import KWSVerification
from .models import WoodchuckProfile


def student_consent_status(session, profile_id):
    """Return a presentation state, or None for a non-active/non-under-13 profile."""
    with session.no_autoflush:
        profile = session.get(WoodchuckProfile, profile_id)
        rule = session.get(AccountPrivacy, profile_id)
        if not profile or profile.status != 'active' or not rule or rule.age_band != 'under13':
            return None
        try:
            notice_version, _, _ = service.notice_policy()
        except (ValueError, RuntimeError):
            return 'status_unknown'
        pending = session.scalar(select(PendingConsent).where(PendingConsent.profile_id == profile_id)
                                 .order_by(PendingConsent.created_at.desc(), PendingConsent.id.desc()).limit(1))
        if pending is None:
            return 'no_flow'
        verification = session.scalar(select(KWSVerification).where(KWSVerification.pending_id == pending.id))
        if verification and verification.state == 'activated':
            if rule.consent_id and verification.activated_consent_id == rule.consent_id:
                evidence = session.get(ConsentEvidence, rule.consent_id)
                if evidence and evidence.profile_id == profile_id and evidence.withdrawn_at:
                    return 'permission_withdrawn'
            return 'permission_paused'
        if utc(pending.expires_at) > service.clock() and pending.notice_version != notice_version:
            return 'request_superseded'
        if verification and verification.state == 'cancelled':
            return 'request_closed'
        if utc(pending.expires_at) <= service.clock():
            return 'expired'
        if verification is None:
            return 'pending_parent'
        if verification.state in ('reserved', 'accepted'):
            return 'kws_pending'
        if verification.state == 'delivery_unknown':
            return 'kws_delivery_unknown'
        if verification.state == 'verified':
            return 'verified_not_activated'
        if verification.state == 'failed':
            return 'kws_failed'
        return 'status_unknown'
