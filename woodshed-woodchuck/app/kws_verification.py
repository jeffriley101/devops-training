"""Signed KWS Test results converge here; permissions come from prior parent choices."""
from datetime import timedelta
import hashlib
import hmac
import json
import re
import secrets
from sqlalchemy import select
from . import child_authorization as consent
from .child_models import PendingConsent
from .age_models import AccountPrivacy
from .models import WoodchuckProfile
from .kws_models import KWSVerification, KWSEmailBudget
from .kws_client import Config, KWSUnavailable, client, secret_list
from .security import hash_invitation_token
from .session_config import session_secret

# Local policy, not a claimed KWS requirement: accept signed results up to 24h
# old with 5m future/creation skew, while the bound request itself lasts at most 48h.
FRESHNESS = timedelta(hours=24)
SKEW = timedelta(minutes=5)
MAX_EMAILS_PER_HOUR = 3  # Conservative subset of KWS's ten/hour; rolling, across workers.
TERMINAL = {'verified', 'activated', 'failed', 'cancelled'}


class InvalidResult(ValueError):
    pass


def fail():
    raise InvalidResult('Invalid, expired or unbound KWS Test result.')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail()
        result[key] = value
    return result


def parse_json(value):
    try:
        result = json.loads(value, object_pairs_hook=unique_object)
        if not isinstance(result, dict):
            fail()
        return result
    except (ValueError, TypeError, UnicodeError):
        fail()


def fresh(timestamp):
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        fail()
    now = consent.clock().timestamp()
    if not now - FRESHNESS.total_seconds() <= timestamp <= now + SKEW.total_seconds():
        fail()
    return timestamp


def signatures_match(message, signatures, secrets_):
    if not 1 <= len(signatures) <= 10:
        fail()
    matched = False
    for key in secrets_:
        expected = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
        for signature in signatures:
            valid = isinstance(signature, str) and bool(re.fullmatch('[0-9a-fA-F]{64}', signature))
            # Compare every candidate without an early successful exit.
            matched |= hmac.compare_digest(expected, signature.lower() if valid else '0' * 64) and valid
    if not matched:
        fail()


def webhook_result(raw, header, cfg):
    if len(raw) > 65536 or not isinstance(header, str) or len(header) > 2048:
        fail()
    parts = [p.strip().split('=', 1) for p in header.split(',')]
    if any(len(p) != 2 for p in parts):
        fail()
    times = [v for k, v in parts if k == 't']
    if len(times) != 1 or not re.fullmatch('[0-9]{1,12}', times[0]):
        fail()
    timestamp = fresh(int(times[0]))
    # The original timestamp and EXACT raw bytes are signed, before any JSON parse.
    signatures_match(times[0].encode() + b'.' + raw,
                     [v for k, v in parts if k == 'v1'], secret_list('KWS_TEST_WEBHOOK_SECRETS'))
    event = parse_json(raw.decode('utf-8', errors='strict'))
    if event.get('name') != 'parent-verified' or event.get('orgId') != cfg.org_id:
        fail()
    if event.get('productId') is not None and event.get('productId') != cfg.product_id:
        fail()
    payload = event.get('payload')
    if not isinstance(payload, dict) or not isinstance(payload.get('parentEmail'), str):
        fail()
    return payload.get('externalPayload'), payload.get('status'), timestamp, payload['parentEmail']


def redirect_result(params):
    if (len(params.getlist('status')) != 1 or len(params.getlist('externalPayload')) != 1
            or set(params) - {'status', 'externalPayload', 'signature'}):
        fail()
    status, payload = params['status'], params['externalPayload']
    if len(status) > 4096 or len(payload) > 250:
        fail()
    # QueryParams has percent-decoded once. Never reserialize the signed strings.
    signatures_match((status + ':' + payload).encode(), params.getlist('signature'),
                     secret_list('KWS_TEST_VERIFICATION_SECRETS'))
    data = parse_json(status)
    return payload, data, fresh(data.get('timestamp')), None


def binding(row, verification):
    values = {'pending_id': row.id, 'profile_id': row.profile_id, 'parent_email': row.parent_email,
              'director_email': row.director_email, 'director_name': row.director_name,
              'review_allowed': row.review_allowed, 'notice_version': row.notice_version,
              'notice_sha256': verification.notice_sha256, 'environment': verification.environment,
              'org_id': verification.org_id, 'product_id': verification.product_id,
              'profile_session_version': verification.profile_session_version,
              'prior_consent_id': verification.prior_consent_id,
              'account_allowed': verification.account_allowed,
              'director_allowed': verification.director_allowed,
              'guardian_attested_at': consent.utc(verification.guardian_attested_at).isoformat(),
              'notice_accepted_at': consent.utc(verification.notice_accepted_at).isoformat()}
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def current_binding(session, row, v, cfg):
    if (v.environment != 'test' or v.org_id != cfg.org_id or v.product_id != cfg.product_id
            or row.notice_version != consent.NOTICE_VERSION or v.notice_sha256 != consent.NOTICE_SHA256
            or not v.account_allowed or not hmac.compare_digest(v.binding_sha256, binding(row, v))):
        fail()
    if row.profile_id:
        profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == row.profile_id)
                                 .with_for_update().execution_options(populate_existing=True))
        rule = session.get(AccountPrivacy, row.profile_id, populate_existing=True)
        if (not profile or profile.status != 'active' or profile.session_version != v.profile_session_version
                or (rule.consent_id if rule else None) != v.prior_consent_id):
            fail()


def reserve_budget(session, email):
    key = hmac.new(session_secret().encode(), ('kws-test-email:' + email.lower()).encode(),
                   hashlib.sha256).hexdigest()
    dialect = session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise KWSUnavailable('KWS Test requires a supported local/Test database.')
    session.execute(insert(KWSEmailBudget).values(email_hash=key, sent_at=[])
                    .on_conflict_do_nothing(index_elements=['email_hash']))
    row = session.scalar(select(KWSEmailBudget).where(KWSEmailBudget.email_hash == key)
                         .with_for_update().execution_options(populate_existing=True))
    now = consent.clock().timestamp()
    history = [t for t in row.sent_at if t > now - 3600]
    if len(history) >= MAX_EMAILS_PER_HOUR or (history and now - max(history) < 300):
        raise ValueError('Wait before requesting another KWS Test email; check the parent inbox.')
    row.sent_at = history + [now]


def start(session, token, fields):
    consent.require_under13_review()
    cfg = Config.load()
    row = consent.pending_from_token(session, token, lock=True)
    if session.scalar(select(KWSVerification.id).where(KWSVerification.pending_id == row.id)):
        raise ValueError('Verification was already requested. Reopen this permission link for its status, or withdraw it.')
    if fields.get('account_allowed') != 'yes':
        row.expires_at = consent.clock()
        session.commit()
        return None
    if (fields.get('guardian_attestation') != 'yes' or fields.get('notice_accepted') != 'yes'
            or fields.get('notice_version') != consent.NOTICE_VERSION):
        raise ValueError('Guardian attestation and explicit acceptance of the current notice are required.')
    from .verifiers import validate_email
    share = fields.get('director_allowed') == 'yes'
    if fields.get('review_allowed') == 'yes' and not share:
        raise ValueError('Chart review requires separately authorized director sharing.')
    if share:
        row.director_email = validate_email(fields.get('director_email', ''))
        row.director_name = str(fields.get('director_name', '')).strip()
        if not 1 <= len(row.director_name) <= 80 or re.search(r'[\x00-\x1f\x7f]', row.director_name):
            raise ValueError('Enter the specific director name without control characters.')
    else:
        row.director_email = row.director_name = ''
    row.review_allowed = share and fields.get('review_allowed') == 'yes'
    profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == row.profile_id)
                             .with_for_update()) if row.profile_id else None
    rule = session.get(AccountPrivacy, row.profile_id) if profile else None
    now = consent.clock()
    payload = secrets.token_urlsafe(32)  # 256 random bits, 43 chars, no unsigned account identifiers.
    v = KWSVerification(pending_id=row.id, payload_hash=hash_invitation_token(payload),
                        environment='test', org_id=cfg.org_id, product_id=cfg.product_id,
                        notice_sha256=consent.NOTICE_SHA256,
                        profile_session_version=profile.session_version if profile else None,
                        prior_consent_id=rule.consent_id if rule else None,
                        account_allowed=True, director_allowed=share, guardian_attested_at=now,
                        notice_accepted_at=now, created_at=now, expires_at=row.expires_at,
                        state='reserved', binding_sha256='')
    v.binding_sha256 = binding(row, v)
    reserve_budget(session, row.parent_email)
    session.add(v)
    session.flush()
    # Persist the binding/budget BEFORE the external side effect. Neither uncertain
    # delivery nor a process restart permits an automatic resend of this request.
    session.commit()
    return v.id, payload


def deliver(session, verification_id, payload):
    v = session.get(KWSVerification, verification_id)
    row = session.get(PendingConsent, v.pending_id)
    try:
        client.send_email(Config.load(), row.parent_email, payload)
        outcome = 'accepted'
    except KWSUnavailable:
        outcome = 'delivery_unknown'
    row = session.scalar(select(PendingConsent).where(PendingConsent.id == v.pending_id)
                         .with_for_update().execution_options(populate_existing=True))
    v = session.scalar(select(KWSVerification).where(KWSVerification.id == verification_id)
                       .with_for_update().execution_options(populate_existing=True))
    if v.state not in TERMINAL:
        v.state = outcome
    session.commit()
    return outcome


def complete(session, cfg, payload, status, timestamp, parent_email=None):
    """No HTTP/email/login here. One transaction, same lock order as withdrawal/activation."""
    consent.require_under13_review()
    if not isinstance(payload, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', payload):
        fail()
    if (not isinstance(status, dict) or type(status.get('verified')) is not bool
            or not isinstance(status.get('transactionId'), str) or not 1 <= len(status['transactionId']) <= 256):
        fail()
    if status['verified'] and status.get('errorCode') not in (None, ''):
        fail()
    pending_id = session.scalar(select(KWSVerification.pending_id)
                                .where(KWSVerification.payload_hash == hash_invitation_token(payload)))
    if not pending_id:
        fail()
    row = session.scalar(select(PendingConsent).where(PendingConsent.id == pending_id)
                         .with_for_update().execution_options(populate_existing=True))
    v = session.scalar(select(KWSVerification).where(KWSVerification.pending_id == pending_id)
                       .with_for_update().execution_options(populate_existing=True))
    if parent_email is not None and not hmac.compare_digest(row.parent_email.lower().encode(), parent_email.lower().encode()):
        fail()
    if timestamp < consent.utc(v.created_at).timestamp() - SKEW.total_seconds():
        fail()
    transaction = hashlib.sha256(json.dumps(['test', cfg.org_id, cfg.product_id,
                                            status['transactionId']], separators=(',', ':')).encode()).hexdigest()
    if v.state in TERMINAL:
        # Already activated/revoked/withdrawn cannot be reconstructed by another delivery.
        if v.state in {'verified', 'activated', 'failed'} and (v.transaction_hash != transaction
                or (v.state in {'verified', 'activated'}) != status['verified']):
            fail()
        return None
    current_binding(session, row, v, cfg)
    if consent.utc(row.expires_at) <= consent.clock() or consent.utc(v.expires_at) <= consent.clock():
        v.state = 'cancelled'
        session.flush()
        return None
    # The DB unique constraint also serializes transaction reuse across different requests.
    if session.scalar(select(KWSVerification.id).where(KWSVerification.transaction_hash == transaction,
                                                      KWSVerification.id != v.id)):
        fail()
    v.transaction_hash = transaction
    v.completed_at = consent.clock()
    if not status['verified']:
        v.state = 'failed'
        session.flush()
        return None
    v.state = 'verified'
    row.approved_at = v.notice_accepted_at
    row.confirmed_at = v.completed_at
    row.confirmation_due = None
    row.activation_hash = hash_invitation_token(consent.derived_token(row, 'activate'))
    row.expires_at = consent.clock() + consent.APPROVED_TTL
    session.flush()
    return consent.derived_token(row, 'activate')


def verified_for_activation(session, row):
    v = session.scalar(select(KWSVerification).where(KWSVerification.pending_id == row.id)
                       .with_for_update().execution_options(populate_existing=True))
    if not v or v.state != 'verified':
        raise ValueError('A completed, current KWS Test verification is required.')
    current_binding(session, row, v, Config.load())
    return v


def cancel(session, row):
    v = session.scalar(select(KWSVerification).where(KWSVerification.pending_id == row.id)
                       .with_for_update().execution_options(populate_existing=True))
    if v and v.state != 'activated':
        v.state = 'cancelled'
    row.expires_at = consent.clock()
    session.flush()
