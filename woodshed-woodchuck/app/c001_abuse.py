"""C001 protection at request and activation boundaries.

PostgreSQL transaction locks serialize each network's rolling windows and the
activation fuse. Short-lived metadata is separate from student/audit history.
Never store request bodies, symbols, raw addresses, or parent information here.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import os
import secrets

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from .db import SessionLocal
from .c001_models import AbuseEvent, ActivationControl

BROWSER_KEY = 'enrollment_abuse_session'
FLASH_KEY = 'secret_symbol_feedback'
_identity = ContextVar('c001_abuse_identity', default=(None, None))
log = logging.getLogger('woodshed.security.c001')



def clock():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def fingerprint(category, value):
    from .session_config import session_secret
    return hmac.new(session_secret().encode(), (category + ':' + value).encode(), hashlib.sha256).hexdigest()


def request_identity(request):
    from .login_limits import source_ip
    from .session_config import is_production
    try:
        if is_production() and os.getenv('RENDER', '').strip().lower() != 'true' and os.getenv('FORWARDED_ALLOW_IPS') != '':
            raise ValueError('Untrusted peer configuration')
        network = fingerprint('c001-network', source_ip(request))
    except ValueError:
        raise HTTPException(503, 'Enrollment protection is temporarily unavailable. Please try again.') from None
    browser = request.session.get(BROWSER_KEY)
    if not isinstance(browser, str) or len(browser) != 32:
        browser = secrets.token_hex(16)
        request.session[BROWSER_KEY] = browser
    return network, fingerprint('c001-browser', browser)


def lock_network(session, network):
    from .session_config import is_production
    if is_production() and session.bind.dialect.name != 'postgresql':
        raise HTTPException(503, 'Enrollment protection is temporarily unavailable.')
    if network and session.bind.dialect.name == 'postgresql':
        key = int.from_bytes(hashlib.sha256(('c001:' + network).encode()).digest()[:8], 'big', signed=True)
        session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def control(session):
    # Also supports create_all disposable test databases. In production the
    # migration seeds this singleton. A locked read refreshes cached ORM state.
    dialect = session.bind.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError('Unsupported activation protection backend')
    session.execute(insert(ActivationControl).values(id=1, enabled=True, emergency_ceiling=1000).on_conflict_do_nothing())
    return session.scalar(select(ActivationControl).where(ActivationControl.id == 1)
                          .with_for_update().execution_options(populate_existing=True))


def activation_enabled():
    # Invalid configuration fails closed. The DB operator switch also applies.
    return os.getenv('C001_ACTIVATION_ENABLED', 'true').strip().lower() in ('true', '1', 'yes', 'on')


def prune(session, now=None):
    session.execute(delete(AbuseEvent).where(AbuseEvent.created_at <= (now or clock()) - timedelta(hours=24)))


def count(session, kind, seconds, now, *, network=None, browser=None):
    query = select(func.coalesce(func.sum(AbuseEvent.occurrences), 0)).where(
        AbuseEvent.kind == kind, AbuseEvent.created_at > now - timedelta(seconds=seconds))
    if network is not None:
        query = query.where(AbuseEvent.network_key == network)
    if browser is not None:
        query = query.where(AbuseEvent.browser_key == browser)
    return session.scalar(query)


def add_event(session, kind, now, network, browser=None, profile_id=None, *, compact=False):
    row = None
    if compact:
        row = session.scalar(select(AbuseEvent).where(
            AbuseEvent.kind == kind, AbuseEvent.network_key == network,
            AbuseEvent.created_at > now - timedelta(minutes=1)).limit(1))
    if row:
        row.occurrences += 1
    else:
        session.add(AbuseEvent(kind=kind, network_key=network, browser_key=browser,
                               profile_id=profile_id, created_at=now))
    session.flush()


def alert(reason, count_value):
    # Deliberately no student, session, network, URL or credential identifiers.
    log.warning('c001_security_event reason=%s count=%d', reason, count_value)


def observe(session, kind, now, network, browser=None, profile_id=None):
    add_event(session, kind, now, network, browser, profile_id, compact=kind in ('rejected', 'age_rejected', 'limited'))
    thresholds = {
        'secret_failure': (600, 20), 'c001_creation': (900, 25),
        'quick_deletion': (3600, 2), 'age_rejected': (600, 2),
        'rejected': (600, 10), 'limited': (600, 2),
    }
    if kind in thresholds:
        seconds, threshold = thresholds[kind]
        total = count(session, kind, seconds, now, network=network)
        if total == threshold + 1:
            alert(kind, total)


def check_attempt(kind, identity, *, session_factory=None):
    network, browser = identity
    limits = [(600, 10, None, browser), (600, 30, network, None)] if kind == 'symbol' else [
        (3600, 5, None, browser), (3600, 40, network, None), (86400, 100, network, None)]
    retry = 0
    with (session_factory or SessionLocal)() as session:
        lock_network(session, network)
        # Requests sharing a browser but changing networks must serialize too.
        lock_network(session, browser)
        now = clock()
        prune(session, now)
        for seconds, maximum, net, br in limits:
            if count(session, kind, seconds, now, network=net, browser=br) >= maximum:
                query = select(func.min(AbuseEvent.created_at)).where(
                    AbuseEvent.kind == kind, AbuseEvent.created_at > now - timedelta(seconds=seconds))
                query = query.where(AbuseEvent.network_key == net) if net else query.where(AbuseEvent.browser_key == br)
                earliest = session.scalar(query)
                retry = max(retry, max(1, int((utc(earliest) + timedelta(seconds=seconds) - now).total_seconds()) + 1))
        if retry:
            observe(session, 'limited', now, network)
        else:
            add_event(session, kind, now, network, browser)
        session.commit()  # Attempts survive validation failures/activation rollback.
    if retry:
        raise HTTPException(429, 'Please wait before trying again. Your registration information is preserved.',
                            headers={'Retry-After': str(retry), 'Cache-Control': 'no-store'})


def record_outcome(kind, identity, *, session_factory=None):
    network, browser = identity
    with (session_factory or SessionLocal)() as session:
        lock_network(session, network)
        prune(session)
        observe(session, kind, clock(), network, browser)
        session.commit()


def active_count(session):
    from .models import TesterEnrollment, WoodchuckProfile
    # Enrollment is created only at activation. A temporary privacy/consent gate
    # does not free its reservation; deletion/deactivation of the account does.
    return session.scalar(select(func.count()).select_from(TesterEnrollment)
        .join(WoodchuckProfile, WoodchuckProfile.id == TesterEnrollment.profile_id)
        .where(TesterEnrollment.cohort_key == 'C001', WoodchuckProfile.status == 'active'))


def authorize_activation(session, profile_id, *, already_enrolled=False):
    network, browser = _identity.get()
    lock_network(session, network)
    settings = control(session)
    if not activation_enabled() or not settings.enabled:
        raise HTTPException(503, 'New C001 activation is temporarily unavailable. Your registration or parent approval is preserved.', headers={'Retry-After': '300'})
    total = active_count(session)
    if not already_enrolled and total >= settings.emergency_ceiling:
        alert('emergency_ceiling', total)
        raise HTTPException(503, 'New C001 activation is temporarily unavailable. Please try again later.', headers={'Retry-After': '3600'})
    now = clock()
    if network:
        network_velocity = count(session, 'activation', 3600, now, network=network)
        if network_velocity > 50:
            alert('network_activation_velocity', network_velocity)
        for seconds, maximum in ((3600, 40), (86400, 100)):
            if count(session, 'activation', seconds, now, network=network) >= maximum:
                raise HTTPException(429, 'C001 activation is temporarily busy for this network. Your registration or parent approval is preserved.', headers={'Retry-After': str(seconds)})
    add_event(session, 'activation', now, network, browser, profile_id)
    for seconds, threshold, net in ((3600, 50, network), (3600, 100, None), (86400, 200, None)):
        if net is None and threshold == 50:
            continue
        velocity = count(session, 'activation', seconds, now, network=net)
        if velocity > threshold:
            alert('network_activation_velocity' if net else 'global_activation_velocity', velocity)
    projected = total + (0 if already_enrolled else 1)
    if projected >= 900:
        alert('high_priority_capacity_review', projected)
    elif projected >= 750:
        alert('capacity_warning', projected)


def record_creation(session, profile_id):
    network, browser = _identity.get()
    observe(session, 'c001_creation', clock(), network, browser, profile_id)


def record_deletion(session, profile_id):
    now = clock()
    # Attribute churn to the creation network even if deletion uses another one.
    event = session.scalar(select(AbuseEvent).where(AbuseEvent.profile_id == profile_id,
        AbuseEvent.kind == 'c001_creation', AbuseEvent.created_at > now - timedelta(hours=1)).limit(1))
    if event:
        lock_network(session, event.network_key)
        observe(session, 'quick_deletion', now, event.network_key)


class EnrollmentProtection:
    """Count before body validation; ContextVar crosses FastAPI worker threads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from starlette.requests import Request
        from starlette.responses import JSONResponse, RedirectResponse
        from starlette.concurrency import run_in_threadpool
        from sqlalchemy.exc import SQLAlchemyError
        path = scope.get('path', '')
        protected = path in ('/account/create', '/account/daily-secret', '/guest/secret-symbol', '/account/age', '/account/delete', '/admin/age/correct') or path.startswith('/family/activate/')
        if scope['type'] != 'http' or scope.get('method') != 'POST' or not protected:
            return await self.app(scope, receive, send)
        from .account_routes import SessionLocal as request_sessions
        request = Request(scope)
        symbol = path in ('/account/daily-secret', '/guest/secret-symbol')
        creation = path == '/account/create' or (path.startswith('/family/activate/') and not request.session.get('woodchuck_profile_id'))
        token = None
        try:
            identity = request_identity(request)
            token = _identity.set(identity)
            if symbol or creation:
                await run_in_threadpool(check_attempt, 'symbol' if symbol else 'creation', identity, session_factory=request_sessions)
            async def outcome(message):
                if message['type'] == 'http.response.start':
                    status = message['status']
                    kind = None
                    if status == 429:
                        kind = 'limited'
                    elif status >= 400:
                        if symbol:
                            kind = 'secret_failure'
                        elif path in ('/account/age',) or path.startswith('/family/activate/') or (creation and status == 403):
                            kind = 'age_rejected'
                        elif creation:
                            kind = 'rejected'
                    if kind:
                        await run_in_threadpool(record_outcome, kind, identity, session_factory=request_sessions)
                await send(message)
            await self.app(scope, receive, outcome)
        except HTTPException as error:
            if path == '/guest/secret-symbol':
                request.session[FLASH_KEY] = error.detail
                response = RedirectResponse('/guest', 303, headers=error.headers)
            else:
                response = JSONResponse({'detail': error.detail}, error.status_code, headers=error.headers)
            await response(scope, receive, send)
        except SQLAlchemyError:
            # No request body or SQL parameters may enter the log.
            alert('protection_unavailable', 1)
            await JSONResponse({'detail': 'Enrollment protection is temporarily unavailable. Please try again.'}, 503)(scope, receive, send)
        finally:
            if token is not None:
                _identity.reset(token)


@asynccontextmanager
async def protection_lifespan(app):
    """Expire protection metadata even during quiet periods (24–25 hour retention)."""
    import asyncio
    from starlette.concurrency import run_in_threadpool
    from sqlalchemy.exc import SQLAlchemyError

    def cleanup():
        with SessionLocal() as session:
            prune(session)
            session.commit()

    async def sweep():
        while True:
            try:
                await run_in_threadpool(cleanup)
            except SQLAlchemyError:
                alert('retention_cleanup_unavailable', 1)
            await asyncio.sleep(3600)

    task = asyncio.create_task(sweep())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main():
    """Explicit operator action; no public control endpoint or background job."""
    import argparse
    parser = argparse.ArgumentParser(description='C001 emergency controls (operator only)')
    parser.add_argument('operation', choices=('status', 'enable', 'disable', 'set-ceiling', 'prune'))
    parser.add_argument('--ceiling', type=int)
    args = parser.parse_args()
    with SessionLocal() as session:
        settings = control(session)
        if args.operation in ('enable', 'disable'):
            settings.enabled = args.operation == 'enable'
        if args.operation == 'set-ceiling':
            if not args.ceiling or args.ceiling < 1:
                parser.error('A positive reviewed --ceiling is required')
            settings.emergency_ceiling = args.ceiling
        if args.operation == 'prune':
            prune(session)
        print(f'enabled={settings.enabled and activation_enabled()} active={active_count(session)} emergency_ceiling={settings.emergency_ceiling}')
        if args.operation != 'status':
            session.commit()
            alert('operator_' + args.operation, settings.emergency_ceiling)


if __name__ == '__main__':
    main()
