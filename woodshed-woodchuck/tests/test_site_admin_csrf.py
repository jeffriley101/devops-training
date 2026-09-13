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


def test_matching_origin_and_host_succeeds():
    check_csrf(request("https://woodshed.example", host="WOodshed.Example"), "token")


def test_proxy_url_netloc_can_differ_from_browser_host():
    check_csrf(request("https://woodshed.example", host="woodshed.example", url_netloc="render-internal:8000"), "token")


def test_external_origin_is_rejected():
    with pytest.raises(HTTPException, match="Invalid request origin"):
        check_csrf(request("https://evil.example"), "token")


def test_invalid_csrf_token_still_rejected():
    with pytest.raises(HTTPException, match="reload the page"):
        check_csrf(request("https://woodshed.example"), "wrong")


def test_forwarded_host_is_used_only_when_host_missing():
    check_csrf(request("https://woodshed.example", host="", forwarded="woodshed.example"), "token")
