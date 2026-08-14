from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import OwnedItemCopy, WoodchuckProfile, WoodchuckState
from .store_catalog import active_catalog_item, item_definition


class InsufficientDandelionsError(ValueError):
    pass


class StoreItemUnavailableError(ValueError):
    pass


class OwnedItemAccessError(ValueError):
    pass


class DecorationCollisionError(ValueError):
    pass


DECORATION_HITBOX_NORMALIZED = 0.10


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
