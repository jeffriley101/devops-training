"""The existing state row holds the balance; only server actions may change it."""
from copy import deepcopy

from sqlalchemy import JSON, String, and_, case, cast, func, literal, or_, select

from .models import WoodchuckProfile, WoodchuckState


def lock_state(session, profile_id):
    """Use profile -> state lock order, including accounts without a state row.

    Refresh cached JSON after waiting for another writer. Flush our own pending
    state first so multiple rewards in one transaction keep their accumulated sum.
    """
    from .age_privacy import require_eligible
    require_eligible(session,profile_id)
    with session.no_autoflush:
        session.execute(select(WoodchuckProfile.id).where(
            WoodchuckProfile.id == profile_id,
        ).with_for_update())
    pending = next((row for row in session.new if isinstance(row, WoodchuckState)
                    and row.profile_id == profile_id), None)
    cached = pending if pending is not None else session.get(WoodchuckState, profile_id)
    if cached is not None and (cached in session.new or session.is_modified(cached)):
        session.flush([cached])
    state = session.scalar(select(WoodchuckState).where(
        WoodchuckState.profile_id == profile_id,
    ).with_for_update().execution_options(populate_existing=True))
    if state is None:
        state = WoodchuckState(profile_id=profile_id, state_json={}, revision=0)
        session.add(state)
        session.flush()
    return state


def preserve_server_values(submitted, saved=None):
    """Keep preferences/legacy state, but never import browser purchasing power."""
    result = deepcopy(submitted)
    saved = saved if isinstance(saved, dict) else {}
    # Ordered quiz answers are server-owned, just like the balance they award.
    result.pop("_history_mystery", None)
    if "_history_mystery" in saved:
        result["_history_mystery"] = deepcopy(saved["_history_mystery"])
    # Appearance is validated only by the dedicated Premium-aware endpoint.
    result.pop("appearance", None)
    if "appearance" in saved:
        result["appearance"] = deepcopy(saved["appearance"])
    prior_progress = saved.get("progress")
    prior_progress = prior_progress if isinstance(prior_progress, dict) else {}
    progress = result.get("progress")
    progress = dict(progress) if isinstance(progress, dict) else {}
    for key, default in (("credits", 0), ("level", 1), ("streak", 0), ("lastCompletedDate", None)):
        progress[key] = deepcopy(prior_progress.get(key, default))
    result["progress"] = progress
    # Current ownership/crowns live in tables. Preserve legacy display copies;
    # synchronization must not mint those either. Equipment remains a preference.
    prior_inventory = saved.get("inventory")
    prior_inventory = prior_inventory if isinstance(prior_inventory, dict) else {}
    inventory = result.get("inventory")
    inventory = dict(inventory) if isinstance(inventory, dict) else {}
    for key in ("ownedItems", "crowns", "medals"):
        if key in inventory or key in prior_inventory:
            inventory[key] = deepcopy(prior_inventory.get(key, []))
    if inventory or "inventory" in result:
        result["inventory"] = inventory
    return result


def economy_payload(state):
    return {"credits": (state.state_json.get("progress") or {}).get("credits", 0),
            "state_revision": state.revision}


def _qualified_wheel_activity_clause():
    """Admit the saved terminal Activity rotation and its exact prize receipt.

    A source label alone is not authority. Match owner, rotation identity,
    amount and earning timestamps without issuing or repairing any reward.
    Guard malformed/non-array legacy JSON before using array operations.
    """
    from .models import CampPointAward, RewardGrant
    from .weekly_models import WeeklyWheelPlay
    outcomes = case(
        (cast(WeeklyWheelPlay.outcomes, String).like("[%"), WeeklyWheelPlay.outcomes),
        else_=literal([], type_=JSON),
    )
    count = func.json_array_length(outcomes)
    last = case((count > 0, count - 1), else_=0)
    source = literal("weekly-wheel:") + WeeklyWheelPlay.id + ":" + cast(count, String)
    receipt = select(RewardGrant.id).join(
        WeeklyWheelPlay, WeeklyWheelPlay.profile_id == RewardGrant.profile_id,
    ).where(
        RewardGrant.profile_id == CampPointAward.profile_id,
        RewardGrant.source_key == CampPointAward.duplicate_key,
        RewardGrant.source_key == source,
        RewardGrant.reward_type == "activity", RewardGrant.amount == 14,
        RewardGrant.contest_result_id.is_(None), RewardGrant.category_key.is_(None),
        RewardGrant.created_at == CampPointAward.occurred_at,
        CampPointAward.created_at == CampPointAward.occurred_at,
        WeeklyWheelPlay.completed_at == CampPointAward.occurred_at,
        WeeklyWheelPlay.funded_at <= WeeklyWheelPlay.completed_at,
        # Both supported databases accept an integer index for ->>. SQLAlchemy's
        # SQLite JSON accessor treats a computed index as an unformatted path.
        count > 0, outcomes.op("->>", return_type=String)(last) == "activity",
    ).correlate(CampPointAward).exists()
    return and_(CampPointAward.activity_type == "wheel-prize",
                CampPointAward.points_awarded == 14, receipt)


def qualified_camp_point_clause():
    """Preserve BOARD provenance and admit only receipted Wheel Activity."""
    from .models import CampPointAward
    return or_(
        _qualified_wheel_activity_clause(),
        CampPointAward.activity_type.in_(("trivia", "contest-placement", "placement")),
        *(
            and_(
                CampPointAward.activity_type == activity,
                CampPointAward.points_awarded == 1,
                CampPointAward.duplicate_key.like(f"board-self-report-v2:____-__-__:{activity}"),
            )
            for activity in ("hours", "care", "marching")
        ),
        and_(
            CampPointAward.activity_type == "quest",
            CampPointAward.points_awarded == 2,
            CampPointAward.duplicate_key.like("bonus-challenge:self-report-v2:____-__-__"),
        ),
    )
