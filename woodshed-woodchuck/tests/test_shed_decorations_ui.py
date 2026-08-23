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
    assert "id=\"shed-decoration-placed-list\"" in HOME
    right_column = scene[scene.index("woodshed-object-column-right"):]
    chair = right_column.index('id="mum-open-button"')
    decorate = right_column.index('id="shed-decorate-button"')
    decorate_end = right_column.index("</button>", decorate)
    decorate_button = right_column[decorate:decorate_end]
    assert chair < decorate
    assert 'class="room-object shed-decorate-button"' in decorate_button
    assert 'aria-label="Decorate the SHED"' in decorate_button
    assert '<span class="room-object-icon" aria-hidden="true">🎨</span>' in decorate_button
    assert "<span>Decorate</span>" not in decorate_button
    assert "Return to Inventory" in DECORATIONS
    panel = HOME[HOME.index('id="shed-decorate-panel"'):HOME.index('id="xp-panel"')]
    assert "Owned Items" not in panel
    assert ">Decorate the SHED<" not in panel
    assert "Tap an inventory item" not in panel
    assert panel.count("<h3>") == 2
    assert ">Inventory<" in panel and ">In the SHED<" in panel
    assert 'id="shed-decorate-close"' in panel
    assert 'id="shed-decoration-feedback" class="sr-only"' in panel


def test_inventory_rows_show_only_emoji_and_required_action() -> None:
    rows = DECORATIONS[DECORATIONS.index("function makeInventoryRow"):DECORATIONS.index("function renderInventory")]
    assert 'document.createElement("strong")' not in rows
    assert "identity.append(emoji)" in rows
    assert 'emoji.setAttribute("role", "img")' in rows
    assert 'button.setAttribute("aria-label", `${actionLabel} ${itemLabel(item)}`)' in rows
    assert '"Place"' in DECORATIONS
    assert '"Return to Inventory"' in DECORATIONS
    assert "placed in the SHED" not in DECORATIONS
    assert "returned to inventory" not in DECORATIONS


def test_owned_inventory_and_placed_items_render_from_server_copies() -> None:
    assert "fetch(\"/store/inventory\"" in DECORATIONS
    assert "ownedItems = Array.isArray(payload.items) ? payload.items : []" in DECORATIONS
    assert "ownedItems.filter((item) => !isPlaced(item))" in DECORATIONS
    assert "ownedItems.filter(isPlaced)" in DECORATIONS
    assert "decoration.dataset.ownedCopyId = String(item.id)" in DECORATIONS
    assert "decoration.textContent = item.emoji" in DECORATIONS
    assert "item.name" in DECORATIONS
    assert "copy ${matching.findIndex" in DECORATIONS


def test_tapping_inventory_places_and_dragging_moves_normalized_coordinates() -> None:
    assert "data-decoration-action" in DECORATIONS
    assert "placeFromInventory(item)" in DECORATIONS
    assert "nextOpenPlacement()" in DECORATIONS
    assert "body: JSON.stringify({ x, y })" in DECORATIONS
    assert "method: \"PUT\"" in DECORATIONS
    assert "left / maxLeft" in DECORATIONS
    assert "top / maxTop" in DECORATIONS
    assert "pointerdown" in DECORATIONS
    assert "pointermove" in DECORATIONS
    assert "pointerup" in DECORATIONS


def test_remove_clears_only_server_placement_and_preserves_copy_ui() -> None:
    remove = DECORATIONS[DECORATIONS.index("async function removePlacement"):DECORATIONS.index("function overlapsPlaced")]
    assert "method: \"DELETE\"" in remove
    assert "/store/inventory/${item.id}/placement" in remove
    assert "updateOwnedItem(payload.item)" in remove


def test_collision_uses_only_other_owned_decorations_and_restores_on_rejection() -> None:
    collision = DECORATIONS[DECORATIONS.index("function overlapsPlaced"):DECORATIONS.index("function nextOpenPlacement")]
    assert "ownedItems.some" in collision
    assert "itemId(item) !== String(ignoredId)" in collision
    assert "Math.abs(item.placement_x - x) < COLLISION_SIZE" in collision
    assert "room-object" not in collision
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


def test_decoration_size_and_phone_layout_are_safe() -> None:
    decoration = CSS[CSS.index(".shed-decoration {"):CSS.index(".woodshed-scene.is-decorating")]
    assert "width: clamp(1.65rem, 3.5vw, 2.25rem)" in decoration
    assert "height: clamp(1.65rem, 3.5vw, 2.25rem)" in decoration
    assert "overflow: hidden" in CSS[CSS.index(".shed-decoration-layer {"):CSS.index(".shed-decoration {")]
    mobile_start = CSS.index("@media (max-width: 480px)", CSS.index(".shed-decoration-error"))
    mobile = CSS[mobile_start:CSS.index(".shed-readout {", mobile_start)]
    assert "grid-template-columns: minmax(0, 1fr)" in mobile
    assert ".shed-decoration-placed-list .shed-decoration-inventory-item" in mobile
    assert "justify-items: center" in mobile
    assert ".shed-decoration-placed-list .shed-decoration-action" in mobile
    assert "width: 100%" in mobile
    assert "overflow-x" not in mobile[:mobile.index("@media", 1) if "@media" in mobile[1:] else len(mobile)]
    decorate_rule = CSS[CSS.index(".shed-decorate-button {"):CSS.index(".shed-decorate-panel {")]
    assert "cursor: pointer" in decorate_rule
    assert "position: absolute" not in decorate_rule
    final_rows = CSS[CSS.index("/* Keep mobile SHED controls"):CSS.index("/* SHED lifetime XP badge")]
    assert ".shed-decorate-button" in final_rows
    assert "top: 67% !important" in final_rows


def test_placed_rows_reserve_separate_emoji_and_action_space() -> None:
    list_start = CSS.index(".shed-decoration-inventory,")
    list_rule = CSS[list_start:CSS.index(".shed-decoration-inventory-item {", list_start)]
    assert "grid-template-columns: minmax(0, 1fr)" in list_rule
    assert "repeat(auto-fit" not in list_rule

    start = CSS.index(".shed-decoration-placed-list .shed-decoration-inventory-item {")
    end = CSS.index(".shed-decoration-inventory-emoji", start)
    placed_rows = CSS[start:end]
    assert "display: grid" in placed_rows
    assert "grid-template-columns: 3rem minmax(10.5rem, 1fr)" in placed_rows
    assert "min-width: 3rem" in placed_rows
    assert "min-width: 10.5rem" in placed_rows
    assert "max-width: 100%" in placed_rows
    assert "justify-self: end" in placed_rows
    assert "white-space: nowrap" in placed_rows

    mobile_start = CSS.index("@media (max-width: 480px)", start)
    mobile = CSS[mobile_start:CSS.index(".shed-readout {", mobile_start)]
    assert "grid-template-columns: minmax(0, 1fr)" in mobile
    assert "min-width: 0" in mobile
    assert "white-space: normal" in mobile

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
    close_rule = CSS[start:CSS.index(".shed-decoration-inventory,", start)]
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
