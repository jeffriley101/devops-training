"""Revoke authenticated browser sessions; anonymous browsing has no server record.

Keep Starlette's existing signed-cookie format and security settings. Suppress its
unchanged-cookie refresh on ordinary responses, and reject retired session nonces
server-side even if a late response or replay restores a previously valid cookie.
"""

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, String, delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse

from .db import Base, SessionLocal

SESSION_LIFETIME = 14 * 24 * 60 * 60
NONCE = "woodshed_auth_session"
ISSUED = "woodshed_auth_issued"
AUTH_KEYS = (
    "woodchuck_profile_id",
    "trusted_verifier_id",
    "site_admin_fingerprint",
    "contest_admin_token_fingerprint",
)


class RevokedBrowserSession(Base):
    __tablename__ = "revoked_browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


def auth_facts(session):
    facts = {key: session[key] for key in AUTH_KEYS if session.get(key)}
    if facts.get("woodchuck_profile_id"):
        facts["woodchuck_session_version"] = session.get("woodchuck_session_version", 0)
    return facts


def digest(nonce):
    return hashlib.sha256(("authenticated-session:" + nonce).encode()).hexdigest()


def revoked(nonce):
    with SessionLocal() as session:
        return session.get(RevokedBrowserSession, digest(nonce)) is not None


def retire(nonce):
    now = datetime.now(timezone.utc)
    key = digest(nonce)
    with SessionLocal() as session:
        if session.get(RevokedBrowserSession, key) is not None:
            return False
        session.add(
            RevokedBrowserSession(
                token_hash=key,
                revoked_at=now,
                expires_at=now + timedelta(seconds=SESSION_LIFETIME + 3600),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if session.get(RevokedBrowserSession, key) is None:
                raise
            return False
        return True


def purge_expired(session, *, now=None):
    """Explicit maintenance helper; no timer, request cleanup or Guest writes."""
    return session.execute(
        delete(RevokedBrowserSession).where(
            RevokedBrowserSession.expires_at < (now or datetime.now(timezone.utc))
        )
    )


class RevocableSessionMiddleware:
    def __init__(self, app, secret_key, session_cookie="session", **kwargs):
        self.app = app
        self.secret = str(secret_key).encode()
        self.cookie = session_cookie.encode()
        self.signed = SessionMiddleware(
            self.authenticated_app,
            secret_key=secret_key,
            session_cookie=session_cookie,
            max_age=SESSION_LIFETIME,
            **kwargs
        )

    async def __call__(self, scope, receive, send):
        async def filtered_send(message):
            if message["type"] == "http.response.start" and scope.get(
                "ww.suppress_session_cookie"
            ):
                message = {
                    **message,
                    "headers": [
                        (key, value)
                        for key, value in message["headers"]
                        if not (
                            key.lower() == b"set-cookie"
                            and value.split(b"=", 1)[0].strip() == self.cookie
                        )
                    ],
                }
            await send(message)

        await self.signed(scope, receive, filtered_send)

    async def authenticated_app(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        session = scope["session"]
        incoming = dict(session)
        initial_facts = auth_facts(session)
        initial_nonce = None
        if initial_facts:
            initial_nonce = session.get(NONCE)
            if not isinstance(initial_nonce, str) or not initial_nonce:
                # Old signed sessions get a deterministic retirement key, so a
                # cookie captured before this deployment cannot evade logout.
                canonical = json.dumps(
                    initial_facts, sort_keys=True, separators=(",", ":")
                )
                initial_nonce = hmac.new(
                    self.secret,
                    ("legacy-session:" + canonical).encode(),
                    hashlib.sha256,
                ).hexdigest()
                session[NONCE] = initial_nonce
                session[ISSUED] = int(time.time())
            issued = session.get(ISSUED)
            valid_age = (
                type(issued) is int and 0 <= time.time() - issued <= SESSION_LIFETIME
            )
            try:
                blocked = not valid_age or await run_in_threadpool(
                    revoked, initial_nonce
                )
            except SQLAlchemyError:
                scope["ww.suppress_session_cookie"] = True
                return await PlainTextResponse(
                    "Session verification is temporarily unavailable. Try again.",
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            if blocked:
                session.clear()
                # A rejected stale request must not erase a newer login's cookie.
                scope["ww.suppress_session_cookie"] = True
                initial_facts = {}
                initial_nonce = None
        else:
            session.pop(NONCE, None)
            session.pop(ISSUED, None)
        abort_body = False

        async def response_send(message):
            nonlocal abort_body
            if abort_body:
                return
            if message["type"] == "http.response.start":
                final_facts = auth_facts(session)
                changed_identity = final_facts != initial_facts
                if initial_nonce and changed_identity:
                    try:
                        first_retirement = await run_in_threadpool(
                            retire, initial_nonce
                        )
                        if final_facts and not first_retirement:
                            scope["ww.suppress_session_cookie"] = True
                            abort_body = True
                            return await PlainTextResponse(
                                "Session changed. Sign in again.",
                                status_code=401,
                                headers={"Cache-Control": "no-store"},
                            )(scope, receive, send)
                    except SQLAlchemyError:
                        # Do not acknowledge logout when its revocation did not persist.
                        scope["ww.suppress_session_cookie"] = True
                        abort_body = True
                        return await PlainTextResponse(
                            "Sign-out could not be confirmed. Try again.",
                            status_code=503,
                            headers={"Cache-Control": "no-store"},
                        )(scope, receive, send)
                if final_facts and (changed_identity or not session.get(NONCE)):
                    session[NONCE] = secrets.token_urlsafe(32)
                    session[ISSUED] = int(time.time())
                    scope["ww.suppress_session_cookie"] = False
                elif not final_facts:
                    session.pop(NONCE, None)
                    session.pop(ISSUED, None)
                # Do not refresh an unchanged signed cookie on late data responses.
                if session == incoming:
                    scope["ww.suppress_session_cookie"] = True
            await send(message)

        await self.app(scope, receive, response_send)
