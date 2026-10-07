"""Purpose-specific, expiring proof for a manually approved Program invitation.

The Site Admin issues this link; possession proves control of its invited mailbox.
It conveys no student relationship or authority until credential proof and the
Program creation transaction both succeed.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re

from .session_config import session_secret
from .verifiers import validate_email


PURPOSE = "classroom-program-founding-director"
VERSION = 1
LIFETIME = timedelta(days=7)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[0-9a-f]{64}$")


@dataclass(frozen=True)
class ProgramInvitation:
    organization_id: int
    email: str
    issued_at: datetime
    expires_at: datetime


def _key() -> bytes:
    # Separate this signature from sessions and other HMAC uses of the secret.
    return hmac.new(session_secret().encode(), b"classroom-program-invitation:v1", hashlib.sha256).digest()


def _encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def create_program_invitation(organization_id: int, email: str, *, now: datetime | None = None) -> str:
    if type(organization_id) is not int or organization_id <= 0:
        raise ValueError("An exact Organization ID is required.")
    normalized = validate_email(email)
    issued = now or datetime.now(timezone.utc)
    if issued.tzinfo is None:
        raise ValueError("Invitation time must have a timezone.")
    issued_at = int(issued.timestamp())
    body = _encode({
        "v": VERSION, "purpose": PURPOSE, "organization_id": organization_id,
        "email": normalized, "iat": issued_at,
        "exp": issued_at + int(LIFETIME.total_seconds()),
    })
    signature = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def parse_program_invitation(token: str, *, now: datetime | None = None) -> ProgramInvitation:
    if not isinstance(token, str) or len(token) > 1024 or not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError("This Program invitation is invalid or expired.")
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("This Program invitation is invalid or expired.")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw)
        if _encode(payload) != body or set(payload) != {"v", "purpose", "organization_id", "email", "iat", "exp"}:
            raise ValueError()
        if type(payload["v"]) is not int or payload["v"] != VERSION or payload["purpose"] != PURPOSE:
            raise ValueError()
        organization_id, email = payload["organization_id"], payload["email"]
        issued_at, expires_at = payload["iat"], payload["exp"]
        if (type(organization_id) is not int or organization_id <= 0
                or not isinstance(email, str) or email != validate_email(email)
                or type(issued_at) is not int or type(expires_at) is not int
                or expires_at - issued_at != int(LIFETIME.total_seconds())):
            raise ValueError()
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or not issued_at <= int(current.timestamp()) < expires_at:
            raise ValueError()
        return ProgramInvitation(
            organization_id, email,
            datetime.fromtimestamp(issued_at, timezone.utc),
            datetime.fromtimestamp(expires_at, timezone.utc),
        )
    except (binascii.Error, TypeError, UnicodeError, OverflowError, ValueError) as error:
        raise ValueError("This Program invitation is invalid or expired.") from error
