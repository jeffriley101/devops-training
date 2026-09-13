"""Independent site administration and CSRF protection for membership writes."""
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlsplit
from fastapi import HTTPException


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
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Invalid request origin.")


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
