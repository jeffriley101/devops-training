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
    assert "Return to Inventory" in DECORATIONS


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
    assert "item.id !== ignoredId" in collision
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
    assert "width: 100%" in mobile
    assert "overflow-x" not in mobile[:mobile.index("@media", 1) if "@media" in mobile[1:] else len(mobile)]


def test_decoration_initialization_is_single_and_profile_controls_remain() -> None:
    assert APP.count("wireShedDecorations();") == 1
    assert "dialog.dataset" not in DECORATIONS
    assert "id=\"instrument-object\"" in HOME
    assert "id=\"xp-level-control\"" in HOME
    assert "id=\"shed-team-button\"" in HOME
    assert "id=\"level-value\"" in HOME
