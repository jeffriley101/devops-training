"""Fail-closed outbound recipient restriction for the isolated KWS Test instance."""
import json
import os
import time
from pathlib import Path
from email.utils import getaddresses


def allowed_recipients():
    try:
        values = json.loads(os.environ['KWS_TEST_RECIPIENTS_JSON'])
        if not isinstance(values, list) or not 1 <= len(values) <= 10:
            return frozenset()
        if any(not isinstance(v, str) or v != v.strip() or '\r' in v or '\n' in v
               or len(v) > 254 or v.count('@') != 1 or any(c in v for c in ' ,;<>')
               or not all(v.split('@')) for v in values):
            return frozenset()
        return frozenset(v.casefold() for v in values)
    except (KeyError, ValueError, TypeError):
        return frozenset()


def recipient_allowed(email):
    return isinstance(email, str) and email.casefold() in allowed_recipients()


def outbound_enabled():
    # Local tunneling starts with delivery disabled even after secrets are entered.
    return (os.getenv('KWS_TEST_RUNTIME_MODE') != 'local'
            or os.getenv('KWS_TEST_OUTBOUND_ENABLED') == 'true')


def bootstrap_local_delivery():
    """Operator-only opt-in; reload app without restarting its disposable DB/tunnel."""
    if os.getenv('KWS_TEST_RUNTIME_MODE') != 'local':
        return
    os.environ['KWS_TEST_OUTBOUND_ENABLED'] = 'false'
    os.environ['KWS_TEST_RECIPIENTS_JSON'] = '[]'
    try:
        root = Path(os.environ['KWS_TEST_DATABASE_SOCKET']).parent
        file = root / 'delivery-authorization.json'
        if (os.getenv('APP_ENV') != 'kws-test' or os.getenv('KWS_ENVIRONMENT') != 'test'
                or root.is_symlink() or file.is_symlink()
                or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077
                or file.stat().st_uid != os.geteuid() or file.stat().st_mode & 0o077
                or file.stat().st_size > 2048):
            return
        policy = json.loads(file.read_text())
        if set(policy) != {'enabled', 'recipient'} or policy['enabled'] is not True:
            return
        os.environ['KWS_TEST_RECIPIENTS_JSON'] = json.dumps([policy['recipient']])
        if not allowed_recipients():
            os.environ['KWS_TEST_RECIPIENTS_JSON'] = '[]'
            return
        os.environ['KWS_TEST_OUTBOUND_ENABLED'] = 'true'
        fd = os.open(root / 'delivery-runtime.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, json.dumps({'app_pid': os.getpid(), 'outbound_enabled': True,
                                    'recipients': sorted(allowed_recipients())}).encode())
        finally:
            os.close(fd)
    except (OSError, ValueError, KeyError, TypeError):
        return  # Missing/invalid private policy keeps delivery closed, not the DB down.


def message_allowed(message):
    if os.getenv('APP_ENV') != 'kws-test':
        return True
    if not outbound_enabled():
        return False
    # Check all envelope recipients, including a possible Resent-* envelope.
    headers = ('To', 'Cc', 'Bcc', 'Resent-To', 'Resent-Cc', 'Resent-Bcc')
    recipients = getaddresses([v for name in headers for v in message.get_all(name, [])])
    return bool(recipients) and all(recipient_allowed(address) for _, address in recipients)


class LocalCallbackEvidence:
    """Private Test delivery evidence; never record body, query, signatures or cookies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        callback = (scope.get('type') == 'http' and
                    (scope.get('method'), scope.get('path')) in {
                        ('POST', '/family/kws/test/webhook'), ('GET', '/family/kws/test/response')})
        async def capture(message):
            if callback and message['type'] == 'http.response.start' and 200 <= message['status'] < 300:
                try:
                    file = Path(os.environ['KWS_TEST_DATABASE_SOCKET']).parent / 'callback-deliveries.jsonl'
                    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                    try:
                        os.fchmod(fd, 0o600)
                        os.write(fd, (json.dumps({'timestamp': time.time(), 'method': scope['method'],
                                                'path': scope['path'], 'status': message['status']})+'\n').encode())
                    finally:
                        os.close(fd)
                except OSError:
                    pass  # Evidence must not alter completion or trigger another provider retry.
            await send(message)
        await self.app(scope, receive, capture)
