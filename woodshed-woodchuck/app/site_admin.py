"""Independent site administration and CSRF protection for membership writes."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from urllib.parse import urlsplit
from fastapi import HTTPException


logger = logging.getLogger(__name__)


def csrf_token(request):
    token = request.session.get("membership_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["membership_csrf"] = token
    return token


def check_csrf(request, supplied):
    expected = request.session.get("membership_csrf", "")
    if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
        _log_admin_origin_diagnostic(request, "csrf_token_rejected")
        raise HTTPException(403, "Please reload the page and try again.")
    origin = request.headers.get("origin")
    if origin:
        origin_endpoint = _origin_endpoint(origin)
        configured = os.getenv("PUBLIC_BASE_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()
        if configured:
            expected_endpoint = _origin_endpoint(configured)
        else:
            scheme = urlsplit(origin).scheme.lower()
            expected_endpoint = _request_host_endpoint(request, scheme)
        if origin_endpoint is None or expected_endpoint is None or origin_endpoint != expected_endpoint:
            reason = ("invalid_submitted_origin" if origin_endpoint is None else
                      "invalid_expected_origin" if expected_endpoint is None else "endpoint_mismatch")
            _log_admin_origin_diagnostic(request, reason)
            raise HTTPException(403, "Invalid request origin.")
    _log_admin_origin_diagnostic(request, "origin_matched" if origin else "origin_absent")


# TEMPORARY: remove this helper and its calls after the Render mismatch is diagnosed.
def _log_admin_origin_diagnostic(request, reason):
    if getattr(request, "method", None) != "POST" or getattr(request.url, "path", None) != "/admin/login":
        return

    def safe_header(value):
        # Preserve normal origin/host headers verbatim. Never log URL credentials,
        # query strings, fragments, control characters, or unbounded input.
        if value is None:
            return None
        if len(value) > 300 or not re.fullmatch(r"[A-Za-z0-9.:/\[\], _-]*", value):
            return "[redacted malformed header]"
        return value

    def config_info(name):
        raw = os.getenv(name)
        value = (raw or "").strip()
        return {"present": raw is not None, "nonempty": bool(value),
                "endpoint": _origin_endpoint(value),
                "surrounding_whitespace": raw is not None and raw != value,
                "boundary_quote": bool(value) and (value[0] in "\"'" or value[-1] in "\"'")}

    public = config_info("PUBLIC_BASE_URL")
    render = config_info("RENDER_EXTERNAL_URL")
    origin = request.headers.get("origin")
    endpoint = _origin_endpoint(origin or "")
    if public["nonempty"]:
        source, expected = "PUBLIC_BASE_URL", public["endpoint"]
    elif render["nonempty"]:
        source, expected = "RENDER_EXTERNAL_URL", render["endpoint"]
    else:
        source = "Host"
        try:
            scheme = urlsplit(origin or "").scheme.lower()
        except ValueError:
            scheme = ""
        expected = _request_host_endpoint(request, scheme)
    commit = os.getenv("RENDER_GIT_COMMIT", "")
    payload = {
        "marker": "ww-admin-origin-diag-v1", "reason": reason,
        "build": commit if re.fullmatch(r"[0-9a-fA-F]{7,40}", commit) else "unavailable",
        "method": "POST", "path": "/admin/login",
        "origin": safe_header(origin), "host": safe_header(request.headers.get("host")),
        "x_forwarded_host": safe_header(request.headers.get("x-forwarded-host")),
        "x_forwarded_proto": safe_header(request.headers.get("x-forwarded-proto")),
        "request_scheme": safe_header(request.url.scheme),
        "request_netloc": safe_header(request.url.netloc),
        "public_base_url": public, "render_external_url": render,
        "submitted_endpoint": endpoint, "expected_source": source, "expected_endpoint": expected,
    }
    logger.warning("SITE_ADMIN_ORIGIN_DIAGNOSTIC %s", json.dumps(payload, sort_keys=True))


def _origin_endpoint(origin):
    """Return a normalized (hostname, port) endpoint for an Origin header."""
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        if parsed.scheme.lower() not in {"http", "https"} or not hostname or parsed.username or parsed.password:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80 if parsed.scheme.lower() == "http" else None
    return (hostname.lower(), port)


def _request_host_endpoint(request, scheme=""):
    """Resolve the request Host for local/development origin checks."""
    host = request.headers.get("host")
    if not host:
        return None
    try:
        parsed = urlsplit(f"//{host}")
        if not parsed.hostname:
            return None
        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 80 if scheme == "http" else None
        return (parsed.hostname.lower(), port)
    except ValueError:
        return None


def _fingerprint(token):
    secret = os.getenv("SESSION_SECRET", "woodshed-local-development-secret")
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def sign_in_site_admin(request, token):
    configured = os.getenv("SITE_ADMIN_TOKEN", "")
    if not configured:
        raise HTTPException(503, "Site administration is unavailable.")
    if not hmac.compare_digest(token.encode(), configured.encode()):
        raise HTTPException(403, "Invalid administrator credentials.")
    request.session["site_admin_fingerprint"] = _fingerprint(configured)
    request.session["membership_csrf"] = secrets.token_urlsafe(32)


def require_site_admin(request):
    configured = os.getenv("SITE_ADMIN_TOKEN", "")
    actual = request.session.get("site_admin_fingerprint")
    if not configured or not isinstance(actual, str) or not hmac.compare_digest(actual, _fingerprint(configured)):
        raise HTTPException(403, "Site administrator sign-in is required.")
