"""Independent site administration and CSRF protection for membership writes."""
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlsplit
from fastapi import HTTPException
from .session_config import session_secret


def csrf_token(request):
    token = request.session.get("membership_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["membership_csrf"] = token
    return token


def check_csrf(request, supplied):
    expected = request.session.get("membership_csrf", "")
    if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, "Please reload the page and try again.")
    origin = request.headers.get("origin")
    if not origin:
        return
    configured = os.getenv("PUBLIC_BASE_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    expected_endpoint = _origin_endpoint(configured) if configured else _request_host_endpoint(request)
    if origin == "null":
        fetch_site = request.headers.get("sec-fetch-site")
        request_endpoint = _request_host_endpoint(request)
        if (fetch_site != "same-origin" or expected_endpoint is None or
                request_endpoint != expected_endpoint):
            raise HTTPException(403, "Invalid request origin.")
        return
    if _origin_endpoint(origin) is None or expected_endpoint is None or _origin_endpoint(origin) != expected_endpoint:
        raise HTTPException(403, "Invalid request origin.")


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
    scheme = scheme or getattr(request.url, "scheme", "")
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
    secret = session_secret()
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
