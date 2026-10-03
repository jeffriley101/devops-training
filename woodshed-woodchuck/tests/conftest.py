from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest


@pytest.fixture(autouse=True)
def first_party_test_clients(monkeypatch):
    """Existing browser-flow tests send an explicit first-party Origin.

    Origin-policy tests override this header (including Origin: '' for missing
    evidence). This supplies request metadata, never bypasses production checks.
    """
    import httpx
    import os
    from starlette.testclient import TestClient
    original = TestClient.__init__

    def initialize(self, *args, **kwargs):
        # Family CSRF tests configure an HTTPS public origin. Model that actual
        # first-party URL, rather than pairing it with TestClient's HTTP default.
        if len(args) < 2:
            kwargs.setdefault("base_url", os.getenv("PUBLIC_BASE_URL") or "http://testserver")
        origin = str(kwargs.get("base_url", args[1] if len(args) > 1 else "http://testserver")).rstrip("/")
        headers = httpx.Headers(kwargs.get("headers") or {})
        headers.setdefault("Origin", origin)
        kwargs["headers"] = headers
        original(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", initialize)
