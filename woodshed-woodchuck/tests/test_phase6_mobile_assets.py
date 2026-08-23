import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
STYLESHEET_URL = "/static/css/styles.css?v=85"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_rendered_pages_use_one_current_stylesheet_version() -> None:
    client = TestClient(app)
    for path in ("/home", "/store"):
        response = client.get(path)
        assert response.status_code == 200
        assert STYLESHEET_URL in response.text

    references = []
    for template in (ROOT / "templates").glob("*.html"):
        references.extend(
            re.findall(r"/static/css/styles\.css\?v=\d+", template.read_text())
        )
    assert references
    assert set(references) == {STYLESHEET_URL}


def test_served_stylesheet_contains_final_mobile_scene_rules() -> None:
    response = TestClient(app).get(STYLESHEET_URL)
    assert response.status_code == 200
    css = response.text

    secret_start = css.index(".woodshed-scene > .shed-secret-button {")
    secret_rule = css[secret_start:css.index("}", secret_start)]
    for declaration in (
        "position: absolute",
        "bottom: max(0.45rem, env(safe-area-inset-bottom))",
        "left: max(0.45rem, env(safe-area-inset-left))",
        "color: #d7263d",
    ):
        assert declaration in secret_rule
    assert css.count(".woodshed-scene > .shed-secret-button {") == 1
    assert "width: 2.25rem" in secret_rule and "height: 2.25rem" in secret_rule



    shop_mobile = css.index("@media (max-width: 430px)", css.index("/* SHOP */"))
    shop_scene = css.index(".shop-scene {", shop_mobile)
    shop_scene_rule = css[shop_scene:css.index("}", shop_scene)]
    assert "display: block" in shop_scene_rule

    shop_columns = css.index(".shop-object-column {", shop_mobile)
    shop_column_rule = css[shop_columns:css.index("}", shop_columns)]
    assert "position: absolute" in shop_column_rule
    assert "display: flex" in shop_column_rule
    assert "flex-direction: column" in shop_column_rule
    assert css.rfind(".shop-object-column {") == shop_columns
    assert css.rfind(".shop-object-column-left {") > shop_columns
    assert css.rfind(".shop-object-column-right {") > shop_columns


def test_no_service_worker_static_cache_is_registered() -> None:
    browser_sources = "\n".join(
        source(str(path.relative_to(ROOT)))
        for directory in (ROOT / "templates", ROOT / "static" / "js")
        for path in directory.glob("*.*")
    )
    assert "serviceWorker.register" not in browser_sources
    assert not list((ROOT / "static").glob("*service*worker*"))
    assert not list((ROOT / "static").glob("sw.js"))
