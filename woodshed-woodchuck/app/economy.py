"""The existing state row holds the balance; only server actions may change it."""
from copy import deepcopy

from sqlalchemy import select

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
