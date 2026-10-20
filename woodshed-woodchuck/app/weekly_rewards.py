"""Small protected-grant adapter and ledger balances; no automatic settlement."""
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
