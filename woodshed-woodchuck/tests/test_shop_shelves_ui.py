from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
STORE = (ROOT / "templates" / "store.html").read_text(encoding="utf-8")
APP = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")
SHOP_WIRING = APP[APP.index("function wireShopPolish"):APP.index("function wirePBook")]


def test_hat_and_juice_open_the_two_catalog_shelves() -> None:
    hat = STORE[STORE.index("🧢") - 220:STORE.index("🧢") + 180]
    juice = STORE[STORE.index("🧃") - 220:STORE.index("🧃") + 190]
    assert "data-shop-panel=\"gear\"" in hat
    assert "Open Gear Shelf" in hat
    assert "data-shop-panel=\"little-buddy\"" in juice
    assert "Open Little Buddy Shelf" in juice
    assert "coming soon" not in (hat + juice).casefold()


def test_each_shelf_renders_exactly_four_server_catalog_items() -> None:
    response = TestClient(app).get("/store/catalog")
    assert response.status_code == 200
    shelves = response.json()["shelves"]
    assert len(shelves["gear"]) == len(shelves["little_buddy"]) == 4
    assert "catalog?.shelves?.gear" in SHOP_WIRING
    assert "catalog?.shelves?.little_buddy" in SHOP_WIRING
    assert "gear.length !== 4" in SHOP_WIRING
    assert "littleBuddy.length !== 4" in SHOP_WIRING
    assert "shelfItems[shelfKey].forEach((item)" in SHOP_WIRING
    for field in ("item.emoji", "item.name", "item.price", "item.item_key"):
        assert field in SHOP_WIRING


def test_rotating_items_and_prices_are_not_client_hardcoded() -> None:
    for server_item in (
        "Candle", "Fruit", "Ice Cream", "Ladybug", "Caterpillar", "Snail",
        "Camp Lantern", "Kite", "Balloon", "Skateboard",
        "Bee", "Butterfly", "Ant", "Beetle",
    ):
        assert server_item not in SHOP_WIRING
    assert "price.textContent = `${item.price} dandelions`" in SHOP_WIRING
    assert "/store/catalog" in SHOP_WIRING
    assert "25 dandelions" not in SHOP_WIRING
    assert "100 dandelions" not in SHOP_WIRING


def test_inventory_counts_and_duplicate_purchase_update_are_immediate() -> None:
    assert "fetch(\"/store/inventory\"" in SHOP_WIRING
    assert "ownedCounts.set(item.item_key, (ownedCounts.get(item.item_key) || 0) + 1)" in SHOP_WIRING
    assert "Owned ×${ownedCounts.get(item.item_key) || 0}" in SHOP_WIRING
    assert "ownedCounts.set(itemKey, (ownedCounts.get(itemKey) || 0) + 1)" in SHOP_WIRING
    assert "credits.textContent = String(payload.dandelion_balance)" in SHOP_WIRING
    assert "purchased. Owned ×${ownedCounts.get(itemKey)}" in SHOP_WIRING


def test_purchase_uses_one_guarded_server_authoritative_request() -> None:
    purchase = SHOP_WIRING[SHOP_WIRING.index("async function purchaseItem"):SHOP_WIRING.index("async function copyPublicAddress")]
    assert purchase.count("fetch(\"/store/purchases\"") == 1
    assert "body: JSON.stringify({ item_key: itemKey })" in purchase
    assert "price" not in purchase[purchase.index("body: JSON.stringify"):purchase.index("});", purchase.index("body: JSON.stringify"))]
    assert "purchaseInFlight.has(itemKey)" in purchase
    assert purchase.index("purchaseInFlight.add(itemKey)") < purchase.index("fetch(\"/store/purchases\"")
    assert "buyButton.disabled = purchaseInFlight.has(item.item_key) || !inventoryAvailable" in SHOP_WIRING
    assert "payload.detail" in purchase
    assert "Not enough dandelions" in purchase
    assert "dialog.dataset.woodshedShopWired" in SHOP_WIRING


def test_share_account_panel_remains_single_and_unchanged() -> None:
    share = STORE[STORE.index("data-shop-panel-content=\"share\""):STORE.index("data-shop-panel-content=\"gear\"")]
    assert "shop-qr-image" in share
    assert "{{ public_site_url }}" in share
    assert "shop-share-account-controls" in share
    assert "Account &amp; Privacy" in share
    assert "id=\"authenticated-logout\"" in share
    assert STORE.count("id=\"authenticated-logout\"") == 1


def test_catalog_cards_are_phone_safe_without_horizontal_overflow() -> None:
    grid = CSS[CSS.index(".shop-catalog-grid {"):CSS.index(".shop-catalog-item {")]
    card = CSS[CSS.index(".shop-catalog-item {"):CSS.index(".shop-catalog-item-emoji {")]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in grid
    assert "min-width: 0" in grid
    assert "min-width: 0" in card
    assert "max-width: calc(100vw - 1.5rem)" in CSS
    assert "width: 100%" in CSS[CSS.index(".shop-buy-button {"):CSS.index(".shop-shelf-status")]
