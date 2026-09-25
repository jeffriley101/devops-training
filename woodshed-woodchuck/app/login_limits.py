"""Atomic, expiring login attempt limits. No credentials enter this module."""
import hashlib
import hmac
import ipaddress
import math
import os
import threading
import time
from functools import lru_cache
from uuid import uuid4

from fastapi import HTTPException

from .session_config import is_production, session_secret


WINDOW_SECONDS = 900
LIMITS = {"student": (10, 100), "verifier": (10, 100), "admin": (None, 5),
          "contest_admin": (None, 5)}
LUA = """
-- Keys are ordered IP first. An IP rejection must not reserve account quota.
for i, key in ipairs(KEYS) do
    local count = redis.call('INCR', key)
    if count == 1 then redis.call('EXPIRE', key, ARGV[1]) end
    local ttl = redis.call('PTTL', key)
    if ttl < 0 then
        redis.call('EXPIRE', key, ARGV[1])
        ttl = tonumber(ARGV[1]) * 1000
    end
    if count > tonumber(ARGV[i + 1]) then return math.max(1, ttl) end
end
return 0
"""


class MemoryBackend:
    """Deterministic local/test backend; never a production fallback."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.rows = {}
        self.lock = threading.Lock()

    def consume(self, keys, limits, window=WINDOW_SECONDS):
        with self.lock:
            now = self.clock()
            self.rows = {key: row for key, row in self.rows.items() if row[1] > now}
            # Same IP-first reservation order as the atomic Redis script.
            for key, limit in zip(keys, limits):
                count, expires = self.rows.get(key, (0, now + window))
                self.rows[key] = (count + 1, expires)
                if count + 1 > limit:
                    return math.ceil((expires - now) * 1000)
            return 0


class RedisBackend:
    def __init__(self, url):
        from redis import Redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        if not url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis-compatible URL required")
        self.client = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1,
                                     retry=Retry(NoBackoff(), 0), decode_responses=True)

    def consume(self, keys, limits, window=WINDOW_SECONDS):
        return int(self.client.eval(LUA, len(keys), *keys, window, *limits))


@lru_cache(maxsize=4)
def _backend(mode, url):
    return RedisBackend(url) if mode == "redis" else MemoryBackend()


def _is_render():
    # This platform marker also enables production policy in session_config.
    return os.getenv("RENDER", "").strip().lower() == "true"


def _settings():
    mode = os.getenv("LOGIN_RATE_LIMIT_MODE", "off").strip().lower()
    required_value = os.getenv("LOGIN_RATE_LIMIT_REQUIRED", "false").strip().lower()
    if required_value not in {"1", "true", "yes", "0", "false", "no"}:
        raise ValueError("Invalid required setting")
    required = required_value in {"1", "true", "yes"}
    url = os.getenv("LOGIN_RATE_LIMIT_REDIS_URL", "")
    if mode not in {"off", "memory", "redis"} or (required and mode != "redis"):
        raise ValueError("Invalid limiter mode")
    if is_production() and mode != "off":
        # Render identity comes from its overwritten CF-Connecting-IP header,
        # independent of Uvicorn's rewritten client. Elsewhere preserve the raw
        # peer for CIDR trust checks, with no contrary launch-command override.
        if mode != "redis":
            raise ValueError("Production requires Redis")
        if not _is_render() and os.environ.get("FORWARDED_ALLOW_IPS") != "":
            raise ValueError("Production requires an unmodified peer address outside Render")
    if mode == "redis" and not url:
        raise ValueError("Missing limiter backend")
    if mode != "off":
        _trusted_proxy_networks()
    return mode, required, url


def _trusted_proxy_networks():
    networks = [ipaddress.ip_network(value.strip()) for value in
                os.getenv("LOGIN_TRUSTED_PROXY_CIDRS", "").split(",") if value.strip()]
    if any(network.prefixlen == 0 for network in networks):
        raise ValueError("Trusting every source as a proxy is unsafe")
    return networks


def source_ip(request):
    if _is_render():
        # Only for Render public ingress, where Cloudflare overwrites this
        # header: https://render.com/articles/host-pocketbase-on-render
        # Never fall back to request.client/XFF: Uvicorn may have rewritten the
        # peer from caller-supplied XFF. Missing/ambiguous headers fail closed.
        values = request.headers.getlist("cf-connecting-ip")
        if len(values) != 1 or "%" in values[0]:
            raise ValueError("Missing or invalid Render client IP")
        parsed = ipaddress.ip_address(values[0].strip(" \t"))
        return str(parsed.ipv4_mapped if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped else parsed)

    def address(value):
        parsed = ipaddress.ip_address(value.strip())
        return parsed.ipv4_mapped if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped else parsed

    networks = _trusted_proxy_networks()
    try:
        peer = address(request.client.host if request.client else "")
    except ValueError:
        return "unknown-peer"  # One conservative bucket, never a supplied header.
    if not any(peer in network for network in networks):
        return str(peer)
    forwarded = request.headers.get("x-forwarded-for", "").split(",")
    if len(forwarded) > 20:
        return str(peer)
    try:
        chain = [address(value) for value in forwarded]
    except ValueError:
        return str(peer)
    # Skip only explicitly trusted proxy hops, from the socket toward the user.
    for candidate in reversed(chain):
        if not any(candidate in network for network in networks):
            return str(candidate)
    return str(peer)


def limiter_keys(kind, identifier, ip):
    account_limit, ip_limit = LIMITS[kind]
    identity = identifier.strip().upper() if kind == "student" else identifier.strip().lower()
    secret = session_secret().encode()
    def key(category, value):
        digest = hmac.new(secret, f"{kind}:{category}:{value}".encode(), hashlib.sha256).hexdigest()
        return f"ww:login:v1:{kind}:{category}:{digest}"
    keys, limits = [key("ip", ip)], [ip_limit]
    if account_limit is not None:
        keys.append(key("account", identity))
        limits.append(account_limit)
    return keys, limits


def enforce_login_limit(request, kind, identifier=""):
    try:
        mode, required, url = _settings()
        if mode == "off":
            return
        keys, limits = limiter_keys(kind, identifier, source_ip(request))
        retry_ms = _backend(mode, url).consume(keys, limits)
    except Exception:
        # Redis errors can contain connection URLs. Never log or return them.
        raise HTTPException(503, "Sign-in protection is temporarily unavailable. Please try again later.") from None
    if retry_ms:
        raise HTTPException(429, "Too many sign-in attempts. Please try again later.",
                            headers={"Retry-After": str(max(1, math.ceil(retry_ms / 1000)))})


def protection_status(check_backend=True):
    """Admin-only diagnostic. Backend readiness is not deployment attestation."""
    mode = os.getenv("LOGIN_RATE_LIMIT_MODE", "off").strip().lower()
    mode = mode if mode in {"off", "memory", "redis"} else "invalid"
    status = {"mode": mode, "configured": mode == "redis" and bool(os.getenv("LOGIN_RATE_LIMIT_REDIS_URL")),
              "required": os.getenv("LOGIN_RATE_LIMIT_REQUIRED", "false").strip().lower()
              in {"1", "true", "yes"}, "production_verified": False}
    try:
        mode, required, url = _settings()
        if mode == "off":
            return {**status, "state": "disabled"}
        if not check_backend:
            return {**status, "state": "configured" if mode == "redis" else "local_only"}
        # Exercise the same script/ACL path, using an expiring diagnostic key.
        _backend(mode, url).consume([f"ww:login:v1:probe:{uuid4().hex}"], [1], window=2)
        return {**status, "state": "backend_operational" if mode == "redis" else "local_only"}
    except Exception:
        return {**status, "state": "unavailable"}
