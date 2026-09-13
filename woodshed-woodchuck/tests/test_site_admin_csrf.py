from types import SimpleNamespace
import json

import pytest
from starlette.datastructures import Headers
from fastapi import HTTPException
from fastapi.testclient import TestClient

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


@pytest.mark.parametrize("public,render,reason,source,status", [
    ('"https://woodshed.example/"', "https://woodshed.example", "invalid_expected_origin", "PUBLIC_BASE_URL", 403),
    ("https://wrong.example", "https://woodshed.example", "endpoint_mismatch", "PUBLIC_BASE_URL", 403),
    (" https://WOODSHED.example:443/path?private=value ", None, "origin_matched", "PUBLIC_BASE_URL", 303),
    (None, "https://woodshed.example/", "origin_matched", "RENDER_EXTERNAL_URL", 303),
    (None, None, "endpoint_mismatch", "Host", 403),
])
def test_real_admin_login_origin_diagnostics(monkeypatch, caplog, public, render, reason, source, status):
    from app.main import app

    for key, value in (("PUBLIC_BASE_URL", public), ("RENDER_EXTERNAL_URL", render)):
        monkeypatch.delenv(key, raising=False)
        if value is not None:
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "never-log-admin-token")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "5f607dd")
    with TestClient(app, base_url="https://internal.example") as client:
        csrf = client.get("/admin/login").context["csrf"]
        cookie = client.cookies.get("session")
        response = client.post("/admin/login", follow_redirects=False,
            headers={"Origin": "https://woodshed.example", "X-Forwarded-Host": "woodshed.example",
                     "X-Forwarded-Proto": "https"},
            data={"csrf": csrf, "token": "never-log-admin-token"})
    assert response.status_code == status
    records = [r.getMessage() for r in caplog.records if "SITE_ADMIN_ORIGIN_DIAGNOSTIC" in r.getMessage()]
    assert len(records) == 1
    payload = json.loads(records[0].split(" ", 1)[1])
    assert payload["marker"] == "ww-admin-origin-diag-v1"
    assert payload["build"] == "5f607dd"
    assert payload["reason"] == reason
    assert payload["expected_source"] == source
    assert payload["host"] == payload["request_netloc"] == "internal.example"
    assert payload["submitted_endpoint"] == ["woodshed.example", 443]
    assert payload["x_forwarded_host"] == "woodshed.example"
    assert payload["x_forwarded_proto"] == "https"
    if public and public.startswith('"'):
        assert payload["public_base_url"]["boundary_quote"] is True
        assert payload["expected_endpoint"] is None
    for secret in (csrf, cookie, "never-log-admin-token", "private=value"):
        assert secret not in records[0]


def test_diagnostics_redact_sensitive_url_components_and_preserve_token_rejection(monkeypatch, caplog):
    from app.main import app

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://user:secret-password@woodshed.example?secret-query")
    with TestClient(app) as client:
        client.get("/admin/login")
        response = client.post("/admin/login", headers={
            "Origin": "https://user:header-secret@woodshed.example?query-secret",
            "X-Forwarded-Host": "user:forwarded-secret@woodshed.example"},
            data={"csrf": "bad-token-secret", "token": "admin-secret"})
    assert response.status_code == 403
    assert "reload the page" in response.text
    assert '"reason": "csrf_token_rejected"' in caplog.text
    assert "[redacted malformed header]" in caplog.text
    for secret in ("secret-password", "secret-query", "header-secret", "query-secret",
                   "forwarded-secret", "bad-token-secret", "admin-secret"):
        assert secret not in caplog.text
