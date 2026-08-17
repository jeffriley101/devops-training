from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import math

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    CrownAward,
    OwnedItemCopy,
    RewardInventoryPlacement,
    WoodchuckProfile,
    WoodchuckState,
)
from .store_catalog import (
    MUM_SNACK_ITEM_KEYS,
    STORE_TIMEZONE,
    active_catalog_item,
    item_definition,
)


class InsufficientDandelionsError(ValueError):
    pass


class StoreItemUnavailableError(ValueError):
    pass


class OwnedItemAccessError(ValueError):
    pass


class DecorationCollisionError(ValueError):
    pass


DECORATION_HITBOX_NORMALIZED = 0.10
CROWN_EMOJI = "👑"
CROWN_NAMES = {
    "weekly-points-leaders": "Practice Crown",
    "weekly-camp-points": "Band Camp Crown",
    "trivia": "Trivia Crown",
    "instrument-care": "Instrument Care Crown",
    "marching": "Marching Crown",
    "band-camp-hours": "Band Camp Hours Crown",
    "team-crown": "Team Crown",
}


def central_week_start(*, now: datetime | None = None) -> date:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_date = instant.astimezone(STORE_TIMEZONE).date()
    return local_date.fromordinal(local_date.toordinal() - local_date.weekday())


def crown_inventory_key(award_id: int) -> str:
    return f"crown:{award_id}"


def crown_award_id_from_inventory_key(inventory_key: str) -> int | None:
    prefix = "crown:"
    if not inventory_key.startswith(prefix):
        return None
    try:
        award_id = int(inventory_key[len(prefix):])
    except ValueError:
        return None
    return award_id if award_id > 0 else None


def crown_name(category_key: str) -> str:
    return CROWN_NAMES.get(category_key, "Crown")


def _credits(state: WoodchuckState | None) -> int:
    if state is None:
        return 0
    value = ((state.state_json or {}).get("progress") or {}).get("credits", 0)
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, value)


def owned_item_payload(item: OwnedItemCopy) -> dict[str, object]:
    definition = item_definition(item.item_key)
    return {
        "id": item.id,
        "item_key": item.item_key,
        "name": definition.name if definition else item.item_key,
        "emoji": definition.emoji if definition else "",
        "shelf": definition.shelf if definition else None,
        "acquisition_source": item.acquisition_source,
        "purchase_price": item.purchase_price,
        "placement_x": item.placement_x,
        "placement_y": item.placement_y,
        "acquired_at": item.acquired_at.isoformat(),
    }


def list_owned_items(session: Session, *, profile_id: int) -> list[OwnedItemCopy]:
    return list(session.scalars(
        select(OwnedItemCopy)
        .where(OwnedItemCopy.profile_id == profile_id)
        .order_by(OwnedItemCopy.acquired_at.asc(), OwnedItemCopy.id.asc())
    ).all())


def crown_inventory_payload(
    award: CrownAward,
    placement: RewardInventoryPlacement | None,
) -> dict[str, object]:
    return {
        "id": crown_inventory_key(award.id),
        "item_key": f"crown:{award.category_key}",
        "name": crown_name(award.category_key),
        "emoji": CROWN_EMOJI,
        "shelf": "earned",
        "acquisition_source": "crown",
        "purchase_price": None,
        "placement_x": placement.placement_x if placement else None,
        "placement_y": placement.placement_y if placement else None,
        "acquired_at": award.earned_at.isoformat(),
    }


def list_inventory_payloads(session: Session, *, profile_id: int) -> list[dict[str, object]]:
    owned_payloads = [
        owned_item_payload(item)
        for item in list_owned_items(session, profile_id=profile_id)
    ]
    earned_crowns = list(session.scalars(
        select(CrownAward)
        .where(CrownAward.profile_id == profile_id)
        .order_by(CrownAward.earned_at.asc(), CrownAward.id.asc())
    ).all())
    placements = {
        placement.crown_award_id: placement
        for placement in session.scalars(
            select(RewardInventoryPlacement).where(
                RewardInventoryPlacement.profile_id == profile_id
            )
        ).all()
    }
    crown_payloads = [
        crown_inventory_payload(award, placements.get(award.id))
        for award in earned_crowns
    ]
    return [*owned_payloads, *crown_payloads]


def _normalized_coordinate(value: float) -> float:
    coordinate = float(value)
    if not math.isfinite(coordinate) or coordinate < 0 or coordinate > 1:
        raise ValueError("Decoration coordinates must be between 0 and 1.")
    return coordinate


def _lock_placement_profile(session: Session, *, profile_id: int) -> None:
    locked_profile_id = session.scalar(
        select(WoodchuckProfile.id)
        .where(WoodchuckProfile.id == profile_id)
        .with_for_update()
    )
    if locked_profile_id is None:
        raise OwnedItemAccessError("That owned item is unavailable.")


def place_owned_item_copy(
    session: Session,
    *,
    profile_id: int,
    owned_item_id: int,
    placement_x: float,
    placement_y: float,
) -> OwnedItemCopy:
    x = _normalized_coordinate(placement_x)
    y = _normalized_coordinate(placement_y)
    _lock_placement_profile(session, profile_id=profile_id)
    owned = session.scalar(
        select(OwnedItemCopy)
        .where(
            OwnedItemCopy.id == owned_item_id,
            OwnedItemCopy.profile_id == profile_id,
        )
        .with_for_update()
    )
    if owned is None:
        raise OwnedItemAccessError("That owned item is unavailable.")

    placed_items = session.scalars(
        select(OwnedItemCopy)
        .where(
            OwnedItemCopy.profile_id == profile_id,
            OwnedItemCopy.id != owned_item_id,
            OwnedItemCopy.placement_x.is_not(None),
            OwnedItemCopy.placement_y.is_not(None),
        )
        .with_for_update()
    ).all()
    for other in placed_items:
        if (
            abs(float(other.placement_x) - x) < DECORATION_HITBOX_NORMALIZED
            and abs(float(other.placement_y) - y) < DECORATION_HITBOX_NORMALIZED
        ):
            raise DecorationCollisionError(
                "That spot overlaps another decoration. Choose another spot."
            )
    reward_placements = session.scalars(
        select(RewardInventoryPlacement)
        .where(RewardInventoryPlacement.profile_id == profile_id)
        .with_for_update()
    ).all()
    for other in reward_placements:
        if (
            abs(float(other.placement_x) - x) < DECORATION_HITBOX_NORMALIZED
            and abs(float(other.placement_y) - y) < DECORATION_HITBOX_NORMALIZED
        ):
            raise DecorationCollisionError(
                "That spot overlaps another decoration. Choose another spot."
            )

    owned.placement_x = x
    owned.placement_y = y
    session.flush()
    return owned


def remove_owned_item_copy_placement(
    session: Session,
    *,
    profile_id: int,
    owned_item_id: int,
) -> OwnedItemCopy:
    _lock_placement_profile(session, profile_id=profile_id)
    owned = session.scalar(
        select(OwnedItemCopy)
        .where(
            OwnedItemCopy.id == owned_item_id,
            OwnedItemCopy.profile_id == profile_id,
        )
        .with_for_update()
    )
    if owned is None:
        raise OwnedItemAccessError("That owned item is unavailable.")
    owned.placement_x = None
    owned.placement_y = None
    session.flush()
    return owned


def _earned_crown_for_key(
    session: Session, *, profile_id: int, inventory_key: str
) -> CrownAward:
    award_id = crown_award_id_from_inventory_key(inventory_key)
    if award_id is None:
        raise OwnedItemAccessError("That reward is unavailable.")
    award = session.scalar(
        select(CrownAward)
        .where(
            CrownAward.id == award_id,
            CrownAward.profile_id == profile_id,
        )
        .with_for_update()
    )
    if award is None:
        raise OwnedItemAccessError("That reward is unavailable.")
    return award


def _placement_collides(
    session: Session,
    *,
    profile_id: int,
    x: float,
    y: float,
    ignored_owned_item_id: int | None = None,
    ignored_crown_award_id: int | None = None,
) -> bool:
    owned = session.scalars(
        select(OwnedItemCopy)
        .where(
            OwnedItemCopy.profile_id == profile_id,
            OwnedItemCopy.placement_x.is_not(None),
            OwnedItemCopy.placement_y.is_not(None),
        )
        .with_for_update()
    ).all()
    for other in owned:
        if other.id == ignored_owned_item_id:
            continue
        if (
            abs(float(other.placement_x) - x) < DECORATION_HITBOX_NORMALIZED
            and abs(float(other.placement_y) - y) < DECORATION_HITBOX_NORMALIZED
        ):
            return True
    rewards = session.scalars(
        select(RewardInventoryPlacement)
        .where(RewardInventoryPlacement.profile_id == profile_id)
        .with_for_update()
    ).all()
    for other in rewards:
        if other.crown_award_id == ignored_crown_award_id:
            continue
        if (
            abs(float(other.placement_x) - x) < DECORATION_HITBOX_NORMALIZED
            and abs(float(other.placement_y) - y) < DECORATION_HITBOX_NORMALIZED
        ):
            return True
    return False


def place_crown_inventory_item(
    session: Session,
    *,
    profile_id: int,
    inventory_key: str,
    placement_x: float,
    placement_y: float,
) -> dict[str, object]:
    x = _normalized_coordinate(placement_x)
    y = _normalized_coordinate(placement_y)
    _lock_placement_profile(session, profile_id=profile_id)
    award = _earned_crown_for_key(
        session, profile_id=profile_id, inventory_key=inventory_key
    )
    if _placement_collides(
        session,
        profile_id=profile_id,
        x=x,
        y=y,
        ignored_crown_award_id=award.id,
    ):
        raise DecorationCollisionError(
            "That spot overlaps another decoration. Choose another spot."
        )
    placement = session.scalar(
        select(RewardInventoryPlacement)
        .where(RewardInventoryPlacement.crown_award_id == award.id)
        .with_for_update()
    )
    if placement is None:
        placement = RewardInventoryPlacement(
            profile_id=profile_id,
            crown_award_id=award.id,
            placement_x=x,
            placement_y=y,
        )
        session.add(placement)
    else:
        placement.placement_x = x
        placement.placement_y = y
    session.flush()
    return crown_inventory_payload(award, placement)


def remove_crown_inventory_placement(
    session: Session, *, profile_id: int, inventory_key: str
) -> dict[str, object]:
    _lock_placement_profile(session, profile_id=profile_id)
    award = _earned_crown_for_key(
        session, profile_id=profile_id, inventory_key=inventory_key
    )
    placement = session.scalar(
        select(RewardInventoryPlacement)
        .where(RewardInventoryPlacement.crown_award_id == award.id)
        .with_for_update()
    )
    if placement is not None:
        session.delete(placement)
        session.flush()
    return crown_inventory_payload(award, None)


def place_inventory_item(
    session: Session,
    *,
    profile_id: int,
    inventory_id: str,
    placement_x: float,
    placement_y: float,
) -> dict[str, object]:
    crown_award_id = crown_award_id_from_inventory_key(inventory_id)
    if crown_award_id is not None:
        return place_crown_inventory_item(
            session,
            profile_id=profile_id,
            inventory_key=inventory_id,
            placement_x=placement_x,
            placement_y=placement_y,
        )
    try:
        owned_item_id = int(inventory_id)
    except (TypeError, ValueError) as error:
        raise OwnedItemAccessError("That owned item is unavailable.") from error
    return owned_item_payload(place_owned_item_copy(
        session,
        profile_id=profile_id,
        owned_item_id=owned_item_id,
        placement_x=placement_x,
        placement_y=placement_y,
    ))


def remove_inventory_item_placement(
    session: Session, *, profile_id: int, inventory_id: str
) -> dict[str, object]:
    if crown_award_id_from_inventory_key(inventory_id) is not None:
        return remove_crown_inventory_placement(
            session, profile_id=profile_id, inventory_key=inventory_id
        )
    try:
        owned_item_id = int(inventory_id)
    except (TypeError, ValueError) as error:
        raise OwnedItemAccessError("That owned item is unavailable.") from error
    return owned_item_payload(remove_owned_item_copy_placement(
        session, profile_id=profile_id, owned_item_id=owned_item_id
    ))


def claim_weekly_mum_snack(
    session: Session,
    *,
    profile_id: int,
    item_key: str,
    now: datetime | None = None,
) -> tuple[OwnedItemCopy, bool, date]:
    if item_key not in MUM_SNACK_ITEM_KEYS:
        raise ValueError("Choose one of Mum's snack options.")
    week_start = central_week_start(now=now)
    grant_key = f"mum-snack:{week_start.isoformat()}"
    existing = session.scalar(select(OwnedItemCopy).where(
        OwnedItemCopy.profile_id == profile_id,
        OwnedItemCopy.acquisition_key == grant_key,
    ))
    if existing is not None:
        return existing, False, week_start
    try:
        owned, created = grant_owned_item(
            session,
            profile_id=profile_id,
            item_key=item_key,
            grant_key=grant_key,
            now=now,
        )
    except ValueError:
        existing = session.scalar(select(OwnedItemCopy).where(
            OwnedItemCopy.profile_id == profile_id,
            OwnedItemCopy.acquisition_key == grant_key,
        ))
        if existing is not None:
            return existing, False, week_start
        raise
    return owned, created, week_start



def purchase_catalog_item(
    session: Session,
    *,
    profile_id: int,
    item_key: str,
    now: datetime | None = None,
) -> tuple[OwnedItemCopy, int]:
    item = active_catalog_item(item_key, now=now)
    if item is None:
        raise StoreItemUnavailableError("That item is not available in today's catalog.")

    state = session.scalar(
        select(WoodchuckState)
        .where(WoodchuckState.profile_id == profile_id)
        .with_for_update()
    )
    balance = _credits(state)
    if balance < item.price:
        raise InsufficientDandelionsError(
            f"Not enough dandelions. {item.name} costs {item.price}."
        )
    if state is None:
        raise InsufficientDandelionsError("Not enough dandelions.")

    payload = deepcopy(state.state_json or {})
    progress = dict(payload.get("progress") or {})
    new_balance = balance - item.price
    progress["credits"] = new_balance
    payload["progress"] = progress
    state.state_json = payload
    state.revision += 1

    acquired_at = now or datetime.now(timezone.utc)
    if acquired_at.tzinfo is None:
        acquired_at = acquired_at.replace(tzinfo=timezone.utc)
    owned = OwnedItemCopy(
        profile_id=profile_id,
        item_key=item.item_key,
        acquisition_source="store",
        acquisition_key=None,
        purchase_price=item.price,
        placement_x=None,
        placement_y=None,
        acquired_at=acquired_at,
    )
    session.add(owned)
    session.flush()
    return owned, new_balance


def grant_owned_item(
    session: Session,
    *,
    profile_id: int,
    item_key: str,
    grant_key: str,
    now: datetime | None = None,
) -> tuple[OwnedItemCopy, bool]:
    if item_definition(item_key) is None:
        raise ValueError("Unknown owned item.")
    normalized_key = grant_key.strip() if isinstance(grant_key, str) else ""
    if not normalized_key or len(normalized_key) > 100:
        raise ValueError("The grant key must be between 1 and 100 characters.")

    existing = session.scalar(select(OwnedItemCopy).where(
        OwnedItemCopy.profile_id == profile_id,
        OwnedItemCopy.acquisition_key == normalized_key,
    ))
    if existing is not None:
        if existing.item_key != item_key or existing.acquisition_source != "mum":
            raise ValueError("That owned-item grant key is already in use.")
        return existing, False

    acquired_at = now or datetime.now(timezone.utc)
    if acquired_at.tzinfo is None:
        acquired_at = acquired_at.replace(tzinfo=timezone.utc)
    owned = OwnedItemCopy(
        profile_id=profile_id,
        item_key=item_key,
        acquisition_source="mum",
        acquisition_key=normalized_key,
        purchase_price=None,
        placement_x=None,
        placement_y=None,
        acquired_at=acquired_at,
    )
    try:
        with session.begin_nested():
            session.add(owned)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(OwnedItemCopy).where(
            OwnedItemCopy.profile_id == profile_id,
            OwnedItemCopy.acquisition_key == normalized_key,
        ))
        if (
            existing is not None
            and existing.item_key == item_key
            and existing.acquisition_source == "mum"
        ):
            return existing, False
        raise
    return owned, True
