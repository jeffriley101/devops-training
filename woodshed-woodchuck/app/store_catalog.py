from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
from zoneinfo import ZoneInfo


STORE_TIMEZONE = ZoneInfo("America/Chicago")
DAILY_ROTATION_COUNT = 2
DAILY_ROTATION_SALTS = {
    "gear": "woodshed-gear-shelf-v1",
    "little_buddy": "woodshed-little-buddy-shelf-v1",
}


@dataclass(frozen=True)
class CatalogItem:
    item_key: str
    name: str
    emoji: str
    price: int
    shelf: str
    rotating: bool = False

    def payload(self) -> dict[str, object]:
        return asdict(self)


GEAR_FIXED = (
    CatalogItem("candle", "Candle", "🕯️", 25, "gear"),
    CatalogItem("fruit", "Apple", "🍎", 50, "gear"),
    CatalogItem("ice-cream", "Ice Cream", "🍦", 75, "gear"),
    CatalogItem("ufo", "UFO", "🛸", 1000, "gear"),
)
GEAR_ROTATION = (
    CatalogItem("camp-lantern", "Camp Lantern", "🏮", 100, "gear", True),
    CatalogItem("kite", "Kite", "🪁", 100, "gear", True),
    CatalogItem("balloon", "Balloon", "🎈", 100, "gear", True),
    CatalogItem("skateboard", "Skateboard", "🛹", 100, "gear", True),
)
LITTLE_BUDDY_FIXED = (
    CatalogItem("ladybug", "Ladybug", "🐞", 25, "little-buddy"),
    CatalogItem("caterpillar", "Caterpillar", "🐛", 50, "little-buddy"),
    CatalogItem("snail", "Snail", "🐌", 75, "little-buddy"),
    CatalogItem("ant", "Ant", "🐜", 100, "little-buddy"),
)
LITTLE_BUDDY_ROTATION = (
    CatalogItem("bee", "Bee", "🐝", 100, "little-buddy", True),
    CatalogItem("butterfly", "Butterfly", "🦋", 100, "little-buddy", True),
    CatalogItem("beetle", "Beetle", "🪲", 100, "little-buddy", True),
)

MUM_SNACK_ITEMS = (
    CatalogItem("mum-apple", "Apple", "🍎", 0, "mum"),
    CatalogItem("mum-banana", "Banana", "🍌", 0, "mum"),
    CatalogItem("mum-cookie", "Cookie", "🍪", 0, "mum"),
    CatalogItem("mum-pretzel", "Pretzel", "🥨", 0, "mum"),
    CatalogItem("mum-strawberry", "Strawberry", "🍓", 0, "mum"),
    CatalogItem("mum-cheese", "Cheese", "🧀", 0, "mum"),
    CatalogItem("mum-watermelon", "Watermelon", "🍉", 0, "mum"),
    CatalogItem("mum-popcorn", "Popcorn", "🍿", 0, "mum"),
)
MUM_SNACK_ITEM_KEYS = frozenset(item.item_key for item in MUM_SNACK_ITEMS)

ALL_ITEMS = {
    item.item_key: item
    for item in (
        *GEAR_FIXED,
        *GEAR_ROTATION,
        *LITTLE_BUDDY_FIXED,
        *LITTLE_BUDDY_ROTATION,
        *MUM_SNACK_ITEMS,
    )
}


def central_activity_date(*, now: datetime | None = None) -> date:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(STORE_TIMEZONE).date()


def daily_rotation_items(
    activity_date: date,
    *,
    category: str,
    pool: tuple[CatalogItem, ...],
    count: int = DAILY_ROTATION_COUNT,
) -> tuple[CatalogItem, ...]:
    stable_pool = tuple(sorted(pool, key=lambda item: item.item_key))
    if count < 1 or len(stable_pool) < count:
        raise ValueError("The daily SHOP rotation pool is too small.")
    if len({item.item_key for item in stable_pool}) != len(stable_pool):
        raise ValueError("The daily SHOP rotation pool contains duplicate item keys.")
    salt = DAILY_ROTATION_SALTS.get(category, f"woodshed-{category}-shelf-v1")
    digest = hashlib.sha256(salt.encode("utf-8")).digest()
    category_offset = int.from_bytes(digest[:4], "big") % len(stable_pool)
    start = (activity_date.toordinal() + category_offset) % len(stable_pool)
    return tuple(
        stable_pool[(start + offset) % len(stable_pool)]
        for offset in range(count)
    )


def catalog_for_date(activity_date: date) -> dict[str, tuple[CatalogItem, ...]]:
    return {
        "gear": (
            *GEAR_FIXED,
            *daily_rotation_items(
                activity_date, category="gear", pool=GEAR_ROTATION,
            ),
        ),
        "little_buddy": (
            *LITTLE_BUDDY_FIXED,
            *daily_rotation_items(
                activity_date,
                category="little_buddy",
                pool=LITTLE_BUDDY_ROTATION,
            ),
        ),
    }


def catalog_payload(*, now: datetime | None = None) -> dict[str, object]:
    activity_date = central_activity_date(now=now)
    shelves = catalog_for_date(activity_date)
    return {
        "activity_date": activity_date.isoformat(),
        "timezone": str(STORE_TIMEZONE),
        "shelves": {
            "gear": [item.payload() for item in shelves["gear"]],
            "little_buddy": [
                item.payload() for item in shelves["little_buddy"]
            ],
        },
    }


def active_catalog_item(item_key: str, *, now: datetime | None = None) -> CatalogItem | None:
    activity_date = central_activity_date(now=now)
    for items in catalog_for_date(activity_date).values():
        for item in items:
            if item.item_key == item_key:
                return item
    return None


def item_definition(item_key: str) -> CatalogItem | None:
    return ALL_ITEMS.get(item_key)
