from __future__ import annotations

import hashlib
import hmac
import secrets


WOODCHUCK_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32


def is_valid_pin(pin: str) -> bool:
    return len(pin) == 4 and pin.isdigit()


def hash_pin(pin: str) -> str:
    if not is_valid_pin(pin):
        raise ValueError("PIN must contain exactly four digits.")

    salt = secrets.token_bytes(16)

    digest = hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LENGTH,
    )

    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            salt.hex(),
            digest.hex(),
        ]
    )


def verify_pin(pin: str, stored_hash: str) -> bool:
    if not is_valid_pin(pin):
        return False

    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored_hash.split("$")

        if algorithm != "scrypt":
            return False

        salt = bytes.fromhex(salt_hex)
        expected_digest = bytes.fromhex(digest_hex)

        actual_digest = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_digest),
        )
    except (TypeError, ValueError):
        return False

    return hmac.compare_digest(actual_digest, expected_digest)


def generate_woodchuck_id() -> str:
    random_part = "".join(
        secrets.choice(WOODCHUCK_ID_ALPHABET)
        for _ in range(8)
    )

    return f"WC-{random_part}"


def generate_invitation_token() -> str:
    """Create the secret token placed in an invitation link."""
    return secrets.token_urlsafe(32)


def hash_invitation_token(token: str) -> str:
    """Create the deterministic hash stored in the database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
