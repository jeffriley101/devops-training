"""Protected grants, ledger balances and the gated first-practice milestone."""
from sqlalchemy import func, select

from .models import RewardGrant
from .weekly import utc_instant


def grant(session, profile_id, source, reward_type, amount=1, *, now=None):
    from .contests import _grant_once
    return _grant_once(session, profile_id=profile_id, result_id=None,
                       source_key=source, reward_type=reward_type, amount=amount,
                       earned_at=utc_instant(now))


def wallet(session, profile_id):
    # Balances are the existing signed ledger, including future spending rows.
    # Do not flush or settle anything merely to read this projection.
    kinds = ("pickled_carrot", "white_clover", "caper_clue")
    totals = dict(session.execute(select(RewardGrant.reward_type, func.sum(RewardGrant.amount)).where(
        RewardGrant.profile_id == profile_id, RewardGrant.reward_type.in_(kinds)
    ).group_by(RewardGrant.reward_type)).all())
    return {kind: int(totals.get(kind, 0)) for kind in kinds}


def streak_request_authorizer(request, profile, *, credential_login=False):
    """Bind only the gated milestone to the already admitted student request.

    A fresh PIN login has no student nonce yet; its authenticated profile/version
    is captured before waiting. Existing student requests require their nonce.
    """
    from .session_revocations import NONCE, RevokedBrowserSession, digest
    profile_id, version = profile.id, profile.session_version
    nonce = request.session.get(NONCE)

    def authorize(session):
        from fastapi import HTTPException
        from .account_routes import current_profile
        from .age_privacy import require_eligible
        from .feature_access import FEATURES
        from .models import WoodchuckProfile
        if not FEATURES["weekly_pants"].enabled:
            return
        from .session_origin import same_origin_mutation
        if not same_origin_mutation(request):
            raise HTTPException(403, "A same-origin request is required.")
        current = session.get(WoodchuckProfile, profile_id, populate_existing=True)
        valid = (current is not None and current.status == "active"
                 and current.session_version == version)
        if not credential_login:
            bound = current_profile(request, session)
            valid = valid and bound is not None and bound.id == profile_id
        expected = request.headers.get("x-woodshed-account")
        valid = valid and (expected is None or current is not None and expected == current.woodchuck_id)
        if nonce is None:
            valid = valid and credential_login
        else:
            valid = valid and isinstance(nonce, str) and bool(nonce) and session.get(
                RevokedBrowserSession, digest(nonce), populate_existing=True) is None
        if not valid:
            request.scope["ww.suppress_session_cookie"] = True
            raise HTTPException(401, "Sign-in changed. Reload this page.")
        require_eligible(session, profile_id)
    return authorize


def award_first_practice_streak(session, profile_id, *, now=None, authorize=None):
    """WL07 lifetime milestone, using existing receipts and protected balance.

    Historical pants_choice and dandelion receipts both settle this source.
    The caller owns the transaction; no practice or reward history is converted.
    """
    from sqlalchemy import update
    from .age_privacy import require_eligible
    from .feature_access import can_use_feature
    from .models import PracticeChart, WoodchuckProfile
    from .weekly import first_seven_date_milestone
    if not can_use_feature(session, profile_id, "weekly_pants"):
        return False
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(WoodchuckProfile).where(WoodchuckProfile.id == profile_id).values(
            id=WoodchuckProfile.id, updated_at=WoodchuckProfile.updated_at))
    session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == profile_id)
                   .with_for_update().execution_options(populate_existing=True))
    require_eligible(session, profile_id)
    from .economy import lock_state
    lock_state(session, profile_id)
    require_eligible(session, profile_id)
    if authorize is not None:
        authorize(session)
    if not can_use_feature(session, profile_id, "weekly_pants"):
        return False
    session.flush()
    source = "first-seven-practice-dates"
    settled = session.scalar(select(RewardGrant.id).where(
        RewardGrant.profile_id == profile_id, RewardGrant.source_key == source,
        RewardGrant.reward_type.in_(("pants_choice", "dandelion")),
    ).limit(1))
    if settled is not None:
        return False
    charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == profile_id)).all()
    if first_seven_date_milestone(charts, now=now) is None:
        return False
    created = grant(session, profile_id, source, "dandelion", 300, now=now)
    if created:
        from .contests import _add_dandelion
        _add_dandelion(session, profile_id, 300)
    if authorize is not None:
        authorize(session)
    return created
