"""Small shared boundary for session signing and related HMAC keys."""
import os


DEVELOPMENT_SECRET = "woodshed-local-development-secret"


def is_production():
    return (os.getenv("RENDER", "").strip().lower() == "true"
            or os.getenv("APP_ENV", "").strip().lower() == "production")


def session_secret():
    secret = os.getenv("SESSION_SECRET", "")
    if is_production():
        if secret.strip() == DEVELOPMENT_SECRET or len(secret.strip()) < 32:
            raise RuntimeError("Production requires a non-development SESSION_SECRET of at least 32 characters.")
    return secret or DEVELOPMENT_SECRET


def secure_session_cookie():
    value = os.getenv("SESSION_COOKIE_SECURE", "true" if is_production() else "false")
    secure = value.lower() in {"1", "true", "yes"}
    if is_production() and not secure:
        raise RuntimeError("Production requires secure session cookies.")
    return secure
