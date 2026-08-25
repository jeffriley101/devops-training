from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "templates" / "home.html").read_text(encoding="utf-8")
APP = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")
DECORATIONS = APP[APP.index("function wireShedDecorations"):APP.index("function wireShedSecret")]


def test_decorate_mode_has_a_dedicated_scene_layer_and_inventory_panel() -> None:
    scene_start = HOME.index("class=\"woodshed-scene\"")
    scene_end = HOME.index("id=\"shed-decorate-panel\"", scene_start)
    scene = HOME[scene_start:scene_end]
    assert "id=\"shed-decoration-layer\"" in scene
    assert "id=\"shed-decorate-button\"" in scene
    assert "aria-controls=\"shed-decorate-panel\"" in scene
    assert "id=\"shed-decoration-inventory\"" in HOME
    assert "id=\"shed-decoration-placed-list\"" not in HOME
    right_column = scene[scene.index("woodshed-object-column-right"):]
    chair = right_column.index('id="mum-open-button"')
    decorate = right_column.index('id="shed-decorate-button"')
    decorate_end = right_column.index("</button>", decorate)
    decorate_button = right_column[decorate:decorate_end]
    assert chair < decorate
    assert 'class="room-object shed-decorate-button"' in decorate_button
    assert 'aria-label="Open Stickerbook"' in decorate_button
    assert '<span class="room-object-icon" aria-hidden="true">🎨</span>' in decorate_button
    assert "<span>Decorate</span>" not in decorate_button
    assert "Return to Inventory" in DECORATIONS
    panel = HOME[HOME.index('id="shed-decorate-panel"'):HOME.index('id="xp-panel"')]
    assert 'id="shed-stickerbook-title">Stickerbook</h2>' in panel
    assert "Tap an inventory item" not in panel
    assert panel.count("<h3>") == 0
    assert "Available Stickers" not in panel and "In the SHED" not in panel
    assert 'id="shed-decorate-close"' in panel
    assert 'id="shed-decoration-feedback" class="sr-only"' in panel


def test_stickerbook_cards_show_identity_source_size_and_required_actions() -> None:
    rows = DECORATIONS[DECORATIONS.index("function makeInventoryRow"):DECORATIONS.index("function renderInventory")]
    assert "identity.append(emoji, details)" in rows
    assert 'emoji.setAttribute("role", "img")' in rows
    assert 'document.createElement("strong")' in rows
    assert 'source.textContent = {' in rows
    assert 'sizeButton.dataset.decorationSize = size' in rows
    assert 'sizeButton.setAttribute("aria-pressed"' in rows
    assert 'displayCheckbox.checked = isPlaced(item)' in rows
    assert 'displayText.textContent = "Displayed above"' in rows
    assert 'if (isPlaced(item)) {' not in rows
    assert 'button.setAttribute("aria-label", `${actionLabel} ${itemLabel(item)}`)' in rows
    assert '"Place"' in DECORATIONS
    assert '"Return to Inventory"' in DECORATIONS
    assert "placed in the SHED" not in DECORATIONS
    assert "returned to inventory" not in DECORATIONS


def test_owned_inventory_and_placed_items_render_from_server_copies() -> None:
    assert "fetch(\"/store/inventory\"" in DECORATIONS
    assert "ownedItems = Array.isArray(payload.items) ? payload.items : []" in DECORATIONS
    inventory = DECORATIONS[
        DECORATIONS.index("function renderInventory"):
        DECORATIONS.index("function renderAll")
    ]
    assert "ownedItems.forEach" in inventory
    assert "ownedItems.filter" not in inventory
    assert 'displayed ? "remove" : "place"' in inventory
    assert 'displayed ? "Return to Inventory" : "Place"' in inventory
    assert "decoration.dataset.ownedCopyId = String(item.id)" in DECORATIONS
    assert "decoration.textContent = item.emoji" in DECORATIONS
    assert "item.name" in DECORATIONS
    assert "copy ${matching.findIndex" in DECORATIONS


def test_tapping_inventory_places_and_dragging_moves_normalized_coordinates() -> None:
    assert "data-decoration-action" in DECORATIONS
    assert "placeFromInventory(item)" in DECORATIONS
    assert "nextOpenPlacement()" in DECORATIONS
    assert "body: JSON.stringify({ x, y, size })" in DECORATIONS
    assert "method: \"PUT\"" in DECORATIONS
    assert "left / maxLeft" in DECORATIONS
    assert "top / maxTop" in DECORATIONS
    assert "layer.clientWidth - element.offsetWidth" in DECORATIONS
    assert "layer.clientHeight - element.offsetHeight" in DECORATIONS
    assert "pointerdown" in DECORATIONS
    assert "pointermove" in DECORATIONS
    assert "pointerup" in DECORATIONS
    assert 'panel.addEventListener("change"' in DECORATIONS
    assert "data-decoration-display-toggle" in DECORATIONS


def test_unplaced_size_choice_is_saved_and_used_when_placed() -> None:
    preference = DECORATIONS[
        DECORATIONS.index("async function savePreferredSize"):
        DECORATIONS.index("function nextOpenPlacement")
    ]
    assert "/store/inventory/${item.id}/size" in preference
    assert 'body: JSON.stringify({ size })' in preference
    assert "updateOwnedItem(payload.item)" in preference
    handler = DECORATIONS[
        DECORATIONS.index('panel.addEventListener("click"'):
        DECORATIONS.index('panel.addEventListener("change"')
    ]
    assert "if (isPlaced(item))" in handler
    assert "savePreferredSize(item, size)" in handler
    assert "nextOpenPlacement()" in DECORATIONS


def test_remove_clears_only_server_placement_and_preserves_copy_ui() -> None:
    remove = DECORATIONS[DECORATIONS.index("async function removePlacement"):DECORATIONS.index("function nextOpenPlacement")]
    assert "method: \"DELETE\"" in remove
    assert "/store/inventory/${item.id}/placement" in remove
    assert "updateOwnedItem(payload.item)" in remove


def test_client_does_not_reject_overlapping_decorations() -> None:
    placement = DECORATIONS[
        DECORATIONS.index("function nextOpenPlacement"):
        DECORATIONS.index("async function refreshInventory")
    ]
    assert "function overlapsPlaced" not in DECORATIONS
    assert "COLLISION_SIZES" not in DECORATIONS
    assert "ownedItems.filter(isPlaced).length" in placement
    assert "placedCount % (positions.length * positions.length)" in placement
    assert "The middle of the SHED is full" not in DECORATIONS
    save = DECORATIONS[DECORATIONS.index("async function savePlacement"):DECORATIONS.index("async function removePlacement")]
    assert "updateOwnedItem(payload.item)" in save
    assert "catch (error)" in save
    assert "finally" in save and "renderAll()" in save


def test_normal_mode_is_visual_only_and_decorate_mode_enables_dragging() -> None:
    assert "if (!decorateMode) return" in DECORATIONS
    assert "document.createElement(decorateMode ? \"button\" : \"span\")" in DECORATIONS
    assert "if (decorateMode) decoration.type = \"button\"" in DECORATIONS
    assert "else decoration.setAttribute(\"role\", \"img\")" in DECORATIONS
    layer = CSS[CSS.index(".shed-decoration-layer {"):CSS.index(".shed-decoration {")]
    decoration = CSS[CSS.index(".shed-decoration {"):CSS.index(".woodshed-scene.is-decorating")]
    active = CSS[CSS.index(".woodshed-scene.is-decorating .shed-decoration-layer"):CSS.index(".shed-decoration:focus-visible")]
    assert "pointer-events: none" in layer
    assert "pointer-events: none" in decoration
    assert "pointer-events: auto" in active
    assert "touch-action: none" in active


def test_stickerbook_grid_and_size_controls_are_phone_safe() -> None:
    assert ".shed-decoration-inventory {" in CSS
    assert "repeat(auto-fill, minmax(min(16rem, 100%), 1fr))" in CSS
    assert "grid-auto-rows: 1fr" in CSS
    assert "min-height: 12.5rem" in CSS
    assert ".shed-decoration-size-controls" in CSS
    assert "grid-template-columns: repeat(3, minmax(44px, 1fr))" in CSS
    assert "min-height: 44px" in CSS
    mobile = CSS[
        CSS.index("@media (max-width: 640px)", CSS.index(".shed-decoration-size-xlarge")):
    ]
    assert ".shed-decoration-inventory {" in mobile
    assert "grid-template-columns: minmax(0, 1fr)" in mobile
    for size in ("medium", "large", "xlarge"):
        assert f".shed-decoration-size-{size}" in CSS
    assert ".shed-decoration-size-small" not in CSS
    assert 'medium: {short: "M", title: "Medium"}' in DECORATIONS
    assert 'large: {short: "L", title: "Large"}' in DECORATIONS
    assert 'xlarge: {short: "XL", title: "Extra Large"}' in DECORATIONS


def test_decoration_sizes_use_medium_large_and_extra_large_shed_width_scale() -> None:
    medium = CSS[
        CSS.index(".shed-decoration-size-medium {"):
        CSS.index(".shed-decoration-size-large {")
    ]
    large = CSS[
        CSS.index(".shed-decoration-size-large {"):
        CSS.index(".shed-decoration-size-xlarge {")
    ]
    xlarge = CSS[
        CSS.index(".shed-decoration-size-xlarge {"):
        CSS.index("@media (max-width: 640px)", CSS.index(".shed-decoration-size-xlarge {"))
    ]
    assert "width: 19%" in medium and "height: 19cqw" in medium
    assert "width: 33%" in large and "height: 33cqw" in large
    assert "width: 47%" in xlarge and "height: 47cqw" in xlarge
    assert "aspect-ratio: 1 / 1" in medium
    assert "aspect-ratio: 1 / 1" in large
    assert "aspect-ratio: 1 / 1" in xlarge
    assert "container-type: inline-size" in CSS[
        CSS.index(".shed-decoration-layer {"):CSS.index(".shed-decoration {")
    ]
    assert 0.19 < 0.33 < 0.47


def test_mobile_artwork_zoom_and_decoration_layer_share_scene_geometry() -> None:
    mobile = CSS[
        CSS.index("@media (max-width: 640px)", CSS.index(".shed-decoration-size-xlarge")):
    ]
    assert "background-size: cover" in mobile
    assert "background-position: center" in mobile
    assert ".shed-decoration-layer" in mobile
    assert "inset: 7% 0" in mobile
    assert "window.addEventListener(\"resize\", renderPlacedDecorations)" in DECORATIONS


def test_decoration_size_and_phone_layout_are_safe() -> None:
    decoration = CSS[CSS.index(".shed-decoration {"):CSS.index(".woodshed-scene.is-decorating")]
    assert "width: clamp(1.65rem, 3.5vw, 2.25rem)" in decoration
    assert "height: clamp(1.65rem, 3.5vw, 2.25rem)" in decoration
    assert "overflow: hidden" in CSS[CSS.index(".shed-decoration-layer {"):CSS.index(".shed-decoration {")]
    mobile_start = CSS.index("@media (max-width: 480px)", CSS.index(".shed-decoration-error"))
    mobile = CSS[mobile_start:CSS.index(".shed-readout {", mobile_start)]
    assert "grid-template-columns: minmax(0, 1fr)" in mobile
    assert ".shed-decoration-placed-list" not in mobile
    assert "overflow-x" not in mobile[:mobile.index("@media", 1) if "@media" in mobile[1:] else len(mobile)]
    decorate_rule = CSS[CSS.index(".shed-decorate-button {"):CSS.index(".shed-decorate-panel {")]
    assert "cursor: pointer" in decorate_rule
    assert "position: absolute" not in decorate_rule
    final_rows = CSS[CSS.index("/* Keep mobile SHED controls"):CSS.index("/* SHED lifetime XP badge")]
    assert ".shed-decorate-button" in final_rows
    assert "top: 67% !important" in final_rows


def test_unified_stickerbook_cards_keep_one_consistent_layout() -> None:
    stickerbook = CSS[CSS.index("/* SHED Stickerbook and discrete decoration sizes */"):]
    assert ".shed-decoration-placed-list" not in stickerbook
    grid = stickerbook[
        stickerbook.index(".shed-decoration-inventory {"):
        stickerbook.index(".shed-decoration-inventory-item {")
    ]
    assert "repeat(auto-fill, minmax(min(16rem, 100%), 1fr))" in grid
    assert "grid-auto-rows: 1fr" in grid
    card = stickerbook[
        stickerbook.index(".shed-decoration-inventory-item {"):
        stickerbook.index(".shed-decoration-inventory-identity {")
    ]
    assert '"identity displayed"' in card
    assert '"controls controls"' in card
    assert "min-height: 12.5rem" in card
    assert ".shed-decoration-display-toggle" in stickerbook
    assert ".shed-decoration-card-controls .shed-decoration-action" in stickerbook

def test_decorate_panel_overrides_the_wide_mentor_card_layout() -> None:
    wide = CSS[CSS.index("@media (min-width: 860px)"):CSS.index(".woodshed-page", CSS.index("@media (min-width: 860px)"))]
    assert ".mentor-card" in wide
    assert "grid-template-columns: 220px 1fr" in wide

    start = CSS.index(".shed-decorate-panel {")
    panel = CSS[start:CSS.index(".shed-decorate-panel-head", start)]
    assert "grid-template-columns: minmax(0, 1fr)" in panel
    assert "align-items: stretch" in panel
    assert "text-align: left" in panel


def test_decorate_close_is_viewport_fixed_above_the_panel() -> None:
    start = CSS.index(".shed-decorate-close {")
    close_rule = CSS[start:CSS.index(".shed-decoration-inventory {", start)]
    assert "position: fixed" in close_rule
    assert "top: max(0.75rem, env(safe-area-inset-top))" in close_rule
    assert "right: max(0.75rem, env(safe-area-inset-right))" in close_rule
    assert "z-index: 1200" in close_rule
    assert "flex:" not in close_rule


def test_decoration_initialization_is_single_and_profile_controls_remain() -> None:
    assert APP.count("wireShedDecorations();") == 1
    assert "dialog.dataset" not in DECORATIONS
    assert "id=\"instrument-object\"" in HOME
    assert "id=\"xp-level-control\"" in HOME
    assert "id=\"shed-team-button\"" in HOME
    assert "id=\"level-value\"" in HOME
