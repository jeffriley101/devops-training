"""Server-only KWS client. Test and Production credentials never share a path."""
import base64
from dataclasses import dataclass
import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from .kws_test_safety import allowed_recipients, recipient_allowed, outbound_enabled

AUTH_URL = 'https://auth.kidswebservices.com/auth/realms/kws/protocol/openid-connect/token'
API_URL = 'https://api.kidswebservices.com/v1/verifications/send-email'
USER_AGENT = 'Woodshed-Woodchuck/KWS-1'


class KWSUnavailable(ValueError):
    pass


def test_enabled():
    # Environment is an explicit assertion, never inferred from either API host.
    return (os.getenv('KWS_TEST_ENABLED') == 'true'
            and os.getenv('KWS_ENVIRONMENT') == 'test'
            and os.getenv('APP_ENV') == 'kws-test'
            and os.getenv('KWS_TEST_DATABASE_CONFIRMED') == 'true')


def production_enabled():
    # Presence of Production credentials is never activation. All three explicit
    # assertions are required and default closed when absent.
    return (os.getenv('KWS_PRODUCTION_ENABLED') == 'true'
            and os.getenv('KWS_PRODUCTION_ENVIRONMENT') == 'production'
            and os.getenv('APP_ENV') == 'production')


def runtime_environment():
    app_environment = os.getenv('APP_ENV')
    if app_environment == 'kws-test':
        return 'test'
    if app_environment == 'production':
        return 'production'
    raise KWSUnavailable('KWS is unavailable in this application environment.')


def secret_list(name, label='KWS'):
    try:
        values = json.loads(os.environ[name])
        if not isinstance(values, list) or not 1 <= len(values) <= 4:
            raise ValueError()
        if any(not isinstance(v, str) or not v for v in values):
            raise ValueError()
        return tuple(values)
    except (KeyError, ValueError, TypeError):
        raise KWSUnavailable(f'{label} callback configuration is unavailable.') from None


@dataclass(frozen=True, repr=False)
class Config:
    environment: str
    client_id: str
    api_key: str
    org_id: str
    product_id: str | None
    location: str
    language: str
    webhook_secrets: tuple[str, ...]
    verification_secrets: tuple[str, ...]

    @classmethod
    def load(cls, environment=None):
        environment = runtime_environment() if environment is None else environment
        if environment not in ('test', 'production'):
            raise KWSUnavailable('KWS environment is invalid.')
        if environment == 'test' and not test_enabled():
            raise KWSUnavailable('KWS Test is disabled or not explicitly isolated.')
        if environment == 'production' and not production_enabled():
            raise KWSUnavailable('KWS Production activation is disabled.')
        prefix = 'KWS_TEST' if environment == 'test' else 'KWS_PRODUCTION'
        label = 'KWS Test' if environment == 'test' else 'KWS Production'
        try:
            client, key, org = (os.environ[n] for n in
                                (f'{prefix}_CLIENT_ID', f'{prefix}_API_KEY', f'{prefix}_ORG_ID'))
            if not all((client, key, org)) or len(org) > 128:
                raise ValueError()
            product = os.getenv(f'{prefix}_PRODUCT_ID') or None
            if product and len(product) > 128:
                raise ValueError()
            # JSON string containing a country/subdivision code; no country default.
            # The documentation's "GB or AD-07" illustrates alternatives.
            location = json.loads(os.environ[f'{prefix}_LOCATION_JSON'])
            if not isinstance(location, str) or not re.fullmatch(r'[A-Z]{2}(?:-[A-Z0-9]{1,3})?', location):
                raise ValueError()
            if environment == 'test' and outbound_enabled() and not allowed_recipients():
                raise ValueError()
            language = os.getenv(f'{prefix}_LANGUAGE', 'en')
            if not language or len(language) > 20:
                raise ValueError()
            webhook_secrets = secret_list(f'{prefix}_WEBHOOK_SECRETS', label)
            verification_secrets = secret_list(f'{prefix}_VERIFICATION_SECRETS', label)
            return cls(environment, client, key, org, product, location, language,
                       webhook_secrets, verification_secrets)
        except (KeyError, ValueError, TypeError):
            raise KWSUnavailable(f'{label} configuration is incomplete.') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward Basic/Bearer credentials to a redirect target.


def transport(url, headers, body):
    req = Request(url, data=body, headers=headers, method='POST')
    try:
        with build_opener(NoRedirect()).open(req, timeout=8) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise KWSUnavailable('KWS returned an unusable response.')
            return response.status, json.loads(raw)
    except HTTPError as error:
        # Do not read or log upstream bodies (could contain email or credentials).
        return error.code, None
    except (URLError, TimeoutError, OSError, ValueError):
        raise KWSUnavailable('KWS delivery could not be confirmed. Do not automatically resend.') from None


class Client:
    def __init__(self):
        self._lock = threading.Lock()
        self._token = None
        self._expires = 0
        self._credential_tag = None

    def token(self, cfg):
        with self._lock:
            if (self._token and time.monotonic() < self._expires
                    and self._credential_tag == (cfg.environment, cfg.client_id, cfg.api_key)):
                return self._token
            headers = {'Authorization': 'Basic ' + base64.b64encode(
                (cfg.client_id + ':' + cfg.api_key).encode()).decode(),
                'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': USER_AGENT}
            body = urlencode({'grant_type': 'client_credentials', 'scope': 'verification'}).encode()
            # Only token requests retry; they cannot send verification emails.
            for attempt in range(3):
                code, response = transport(AUTH_URL, headers, body)
                if code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(0.25 * 2 ** attempt)
                    continue
                try:
                    token = response['access_token']
                    expiry = int(response['expires_in'])
                    if code != 200 or not isinstance(token, str) or not token or expiry <= 0:
                        raise ValueError()
                except (TypeError, KeyError, ValueError, OverflowError):
                    raise KWSUnavailable('KWS authentication failed.') from None
                self._token = token
                self._expires = time.monotonic() + max(0, expiry - min(30, expiry / 10))
                self._credential_tag = (cfg.environment, cfg.client_id, cfg.api_key)
                return token
            raise KWSUnavailable('KWS authentication failed.')

    def send_email(self, cfg, email, payload):
        if not isinstance(cfg, Config):
            raise KWSUnavailable('KWS configuration is unavailable. No request was sent.')
        if cfg.environment == 'test' and (not test_enabled() or not outbound_enabled()
                                          or not recipient_allowed(email)):
            raise KWSUnavailable('KWS Test recipient is not designated. No request was sent.')
        if cfg.environment == 'production' and not production_enabled():
            raise KWSUnavailable('KWS Production activation is disabled. No request was sent.')
        if cfg.environment not in ('test', 'production'):
            raise KWSUnavailable('KWS environment is invalid. No request was sent.')
        headers = {'Authorization': 'Bearer ' + self.token(cfg),
                   'Content-Type': 'application/json', 'User-Agent': USER_AGENT}
        body = json.dumps({'email': email, 'location': cfg.location, 'language': cfg.language,
                           'userContext': 'parent', 'externalPayload': payload}).encode()
        # Exactly one send attempt. A timeout/5xx may already have sent an email;
        # retries here would flood recipients. A new explicit request uses the DB budget.
        code, response = transport(API_URL, headers, body)
        if (200 <= code < 300 and isinstance(response, dict)
                and isinstance(response.get('response'), dict)
                and response['response'].get('trustEmailRequestAccepted') is True):
            return True
        raise KWSUnavailable('KWS did not confirm acceptance. No verification or permission is implied.')


client = Client()
