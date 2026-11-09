"""S4: expiring Class context, never student authentication or saved practice.

Reuse signed browser sessions, S2 code/authority checks and the existing login
limiter's transient backend. Production requires that existing Redis backend;
memory is explicitly local-only. Nothing in this module commits a SQL write.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from . import classroom_s2 as s2, login_limits
from .c001_abuse import BROWSER_KEY, FLASH_KEY
from .db import SessionLocal
from .session_config import session_secret
from .tester_enrollments import SESSION_REGISTRATION_CONTEXT

router = APIRouter(prefix='/guest/classroom')
KEY = 'classroom_guest_context'
LIFETIME = 900
UNAVAILABLE = 'Guest Classroom is unavailable. Use a current Program and Class code.'
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
ALLOWED_SESSION = {KEY, BROWSER_KEY, FLASH_KEY, SESSION_REGISTRATION_CONTEXT}


def enabled():
    return s2.enabled() and os.getenv('CLASSROOM_GUEST_ENABLED') == '1'


def deny(status=403):
    raise HTTPException(status, UNAVAILABLE, headers=HEADERS)


def now():
    return time.time()


class MemoryContexts:
    """Disposable local counterpart of Redis's atomic, expiring operations."""
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def exchange(self, key, old, new):
        with self.lock:
            self.rows = {k: v for k, v in self.rows.items() if v[1] > now()}
            current = self.rows.get(key, (None, 0))[0]
            if old != current:
                return False
            if new is None:
                self.rows.pop(key, None)
            else:
                self.rows[key] = (new, now() + LIFETIME)
            return True

    def get(self, key):
        with self.lock:
            value, expires = self.rows.get(key, (None, 0))
            return value if expires > now() else None

    def delete(self, key):
        with self.lock:
            self.rows.pop(key, None)


class RedisContexts:
    # Compare-and-set cannot revive an expired/deleted/replaced lease, including
    # a revalidation that waited on a Program lock during logout or login.
    LUA = """
    local found = redis.call('GET', KEYS[1])
    if (found or '') ~= ARGV[1] then return 0 end
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
    """

    def __init__(self, client):
        self.client = client

    def exchange(self, key, old, new):
        return bool(self.client.eval(self.LUA, 1, key, old or '', new, LIFETIME))

    def get(self, key):
        return self.client.get(key)

    def delete(self, key):
        self.client.delete(key)


_memory = MemoryContexts()


def backend():
    mode, _, url = login_limits._settings()
    if mode == 'redis':
        return RedisContexts(login_limits._backend(mode, url).client)
    if mode == 'memory' and not login_limits.is_production():
        return _memory
    raise ValueError('S4 requires existing transient protection')


def key(nonce):
    return 'ww:classroom-guest:v1:' + hmac.new(
        session_secret().encode(), nonce.encode(), hashlib.sha256).hexdigest()


def forget(nonce):
    if isinstance(nonce, str) and len(nonce) == 43:
        backend().delete(key(nonce))


def limit(request, operation):
    # IP-level quotas survive cookie clearing; no code, IP or student identity
    # is retained in cleartext. Entry is bounded even for malformed requests.
    try:
        mode, _, url = login_limits._settings()
        backend()  # In particular, mode=off is never an S4 bypass.
        identity = login_limits.source_ip(request)
        bucket = key('limit:' + operation + ':' + identity)
        maximum = 100 if operation == 'admit' else 6000
        retry = login_limits._backend(mode, url).consume([bucket], [maximum])
    except Exception:
        deny(503)
    if retry:
        raise HTTPException(429, UNAVAILABLE,
                            headers={**HEADERS, 'Retry-After': str(max(1, (retry + 999) // 1000))})


def anonymous(request):
    if set(request.session) - ALLOWED_SESSION:
        deny()


def code_authority(session, program, *, code=None, context=None):
    if not enabled():
        raise s2.ClassroomDenied(UNAVAILABLE)
    if type(program) is not int or not 0 < program < 2**31:
        raise s2.ClassroomDenied(UNAVAILABLE)
    if context is None:
        row = s2._entry(session, program, code=code)
    else:
        s2.s1._lock_program(session, program)
        s2._paid(session, program)
        row = session.get(s2.ClassroomEntryCode, context['code'], populate_existing=True)
        if (row is None or row.program_id != program or row.class_id != context['class']
                or row.generation != context['generation']):
            raise s2.ClassroomDenied(UNAVAILABLE)
        s2._active(session, program, row.class_id, enrollment=True)
    if not row.is_current or row.revoked_at is not None or s2.utc(row.issued_at) > s2._now():
        raise s2.ClassroomDenied(UNAVAILABLE)
    # Recheck time-sensitive entitlement after any Class/code lock wait.
    s2._paid(session, program)
    return row


def pack(context):
    return json.dumps(context, sort_keys=True, separators=(',', ':'))


def perform(request, operation, data):
    anonymous(request)
    store = backend()
    if operation == 'admit':
        if set(data) != {'program', 'code'} or not isinstance(data['code'], str):
            deny()
        with SessionLocal() as session:
            row = code_authority(session, data['program'], code=data['code'])
            context = dict(program=row.program_id, **{'class': row.class_id},
                           code=row.id, generation=row.generation,
                           binding=secrets.token_urlsafe(32), expires=now() + LIFETIME)
            nonce = secrets.token_urlsafe(32)
            # Issue only while the existing Program lock still protects authority.
            if not store.exchange(key(nonce), None, pack(context)):
                deny()
        forget(request.session.get(KEY))
        request.session[KEY] = nonce
    else:
        if set(data) != {'binding'} or not isinstance(data['binding'], str):
            deny()
        nonce = request.session.get(KEY)
        if not isinstance(nonce, str) or len(nonce) != 43:
            deny()
        raw = store.get(key(nonce))
        context = json.loads(raw) if raw else None
        if (not context or not hmac.compare_digest(context['binding'], data['binding'])
                or now() >= context['expires']):
            deny()
        if operation == 'leave':
            forget(nonce)
            request.session.pop(KEY, None)
            return {'active': False}
        with SessionLocal() as session:
            code_authority(session, context['program'], context=context)
            # An expired context cannot be revived after a slow authority read.
            if now() >= context['expires']:
                deny()
            context['expires'] = now() + LIFETIME
            if not store.exchange(key(nonce), raw, pack(context)):
                deny()
    # No Program/Class labels, IDs, school evidence or student data are projected.
    return {'active': True, 'binding': context['binding'], 'expires_in': LIFETIME}


@router.post('/{operation}')
async def operate(request: Request, operation: str):
    from starlette.responses import JSONResponse
    # Turning the feature off denies admission/use, but must still permit
    # retirement of an already issued context. Exit grants no capability.
    if operation not in {'admit', 'check', 'leave'} or (not enabled() and operation != 'leave'):
        deny(404)
    # Stricter than native-form Origin:null compatibility: S4 uses JSON only.
    if (request.headers.get('origin') != str(request.base_url).rstrip('/')
            or request.headers.get('sec-fetch-site', 'same-origin') != 'same-origin'
            or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
        deny()
    await run_in_threadpool(limit, request, operation)
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 512:
            deny()
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            deny()
        result = await run_in_threadpool(perform, request, operation, data)
    except (s2.ClassroomDenied, ValueError, TypeError, KeyError):
        deny()
    except SQLAlchemyError:
        deny(503)
    except HTTPException:
        raise
    except Exception:
        # Backend errors can contain secrets; never log or reflect them.
        deny(503)
    return JSONResponse(result, headers=HEADERS)
