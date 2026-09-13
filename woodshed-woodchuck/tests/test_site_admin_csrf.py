from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers
from fastapi import HTTPException

from app.site_admin import check_csrf


def request(origin, host="woodshed.example", url_netloc="internal:8000", forwarded=None):
    headers = {"host": host}
    if origin is not None:
        headers["origin"] = origin
    if forwarded is not None:
        headers["x-forwarded-host"] = forwarded
    return SimpleNamespace(
        headers=Headers(headers),
        session={"membership_csrf": "token"},
        url=SimpleNamespace(netloc=url_netloc),
    )


def test_matching_origin_and_host_succeeds(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    check_csrf(request("https://woodshed.example", host="WOodshed.Example"), "token")


def test_public_base_url_wins_over_proxy_host(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://woodshed.example/app?from=render")
    check_csrf(request("https://woodshed.example", host="woodshed.example", url_netloc="render-internal:8000"), "token")


def test_public_base_url_mismatch_is_rejected(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://woodshed.example")
    with pytest.raises(HTTPException, match="Invalid request origin"):
        check_csrf(request("https://evil.example"), "token")


def test_render_external_url_is_used_without_public_base_url(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://woodshed.onrender.com/anything")
    check_csrf(request("https://woodshed.onrender.com", host="internal:8000"), "token")


def test_external_origin_is_rejected(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    with pytest.raises(HTTPException, match="Invalid request origin"):
        check_csrf(request("https://evil.example"), "token")


def test_invalid_csrf_token_still_rejected(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://woodshed.example")
    with pytest.raises(HTTPException, match="reload the page"):
        check_csrf(request("https://woodshed.example"), "wrong")


def test_host_fallback_works_without_configured_public_origin(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    check_csrf(request("https://woodshed.example", host="woodshed.example"), "token")


def test_malformed_configured_origin_fails_closed(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "not a url")
    with pytest.raises(HTTPException, match="Invalid request origin"):
        check_csrf(request("https://woodshed.example"), "token")
