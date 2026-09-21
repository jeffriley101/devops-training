"""Guard the proposed Test-only migration/start commands before any DB import."""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from sqlalchemy.engine import make_url
from .kws_test_safety import allowed_recipients, outbound_enabled


def validate_database():
    """Local mode permits only the named disposable database's Unix socket."""
    try:
        db = make_url(os.environ['DATABASE_URL'])
        if os.environ['APP_ENV'] != 'kws-test' or os.environ['KWS_ENVIRONMENT'] != 'test':
            raise ValueError()
        if os.environ['KWS_TEST_DATABASE_CONFIRMED'] != 'true':
            raise ValueError()
        if os.getenv('KWS_TEST_RUNTIME_MODE') == 'local':
            socket = Path(os.environ['KWS_TEST_DATABASE_SOCKET'])
            if (not socket.is_absolute() or not socket.is_dir() or db.host or db.port
                    or db.get_backend_name() != 'postgresql' or db.database != 'woodshed_kws_test'
                    or db.query.get('host') != str(socket) or set(db.query) != {'host'}):
                raise ValueError()
        elif (db.get_backend_name() not in ('postgres', 'postgresql')
              or db.database != 'woodshed_kws_test' or not db.host
              or db.host != os.environ['KWS_TEST_DATABASE_HOST']):
            raise ValueError()
    except Exception:
        raise ValueError('KWS Test database validation failed; no database connection attempted.') from None


def validate():
    try:
        validate_database()
        origin = urlsplit(os.environ['PUBLIC_BASE_URL'])
        local = os.getenv('KWS_TEST_RUNTIME_MODE') == 'local'
        approved_host = (bool(origin.hostname) and origin.hostname == os.getenv('KWS_TEST_PUBLIC_HOST')) if local else (
            bool(origin.hostname) and origin.hostname.startswith('woodshed-kws-test-')
            and origin.hostname.endswith('.onrender.com'))
        checks = (os.environ['APP_ENV'] == 'kws-test', os.environ['KWS_ENVIRONMENT'] == 'test',
                  os.environ['KWS_TEST_DATABASE_CONFIRMED'] == 'true',
                  len(os.environ['SESSION_SECRET']) >= 32,
                  os.environ['SESSION_COOKIE_SECURE'] == 'true',
                  origin.scheme == 'https' and bool(origin.hostname),
                  approved_host,
                  not origin.username and not origin.password and not origin.query and not origin.fragment,
                  origin.path in ('', '/') and origin.port in (None, 443),
                  not outbound_enabled() or bool(allowed_recipients()))
        if not all(checks):
            raise ValueError()
    except Exception:
        raise ValueError('Isolated KWS Test runtime configuration failed validation; no database connection attempted.') from None


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('check', 'migrate', 'serve'):
        raise SystemExit('Use check, migrate or serve.')
    validate()
    if sys.argv[1] == 'check':
        print('KWS Test isolation settings validated; no database connection attempted.')
    elif sys.argv[1] == 'migrate':
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    else:
        os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'app.main:app',
                                 '--host', '127.0.0.1' if os.getenv('KWS_TEST_RUNTIME_MODE') == 'local' else '0.0.0.0',
                                 '--port', os.environ['PORT'], '--no-access-log'])


if __name__ == '__main__':
    main()
