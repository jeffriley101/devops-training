"""Issue one explicitly approved Stripe checkout from a trusted operator shell."""
import argparse
import re
import sys

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from . import billing_providers as billing
from .billing_config import BillingConfig, new_subscription_plan
from .db import SessionLocal
from .memberships import Actor, billing_account, lock_billing_account, utc
from .models import BillingAccount, CheckoutAttempt, MembershipAuditEvent
from .stripe_billing import PLAN_ENV, StripeSettings


AUTHORIZATION = "operator_stripe_checkout_authorized"


def _settings(config, settings, mode, plan_code):
    if config.public_subscriptions_enabled or not config.stripe_billing_enabled:
        raise ValueError("Operator checkout requires public purchasing off and Stripe billing on.")
    if settings is None or settings.mode != mode or plan_code not in settings.prices:
        raise ValueError("The approved Stripe mode and plan must be configured.")
    return settings


def _lock_authorization(session):
    # The first approval must serialize even when two operators select different owners.
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        # Read the prior winner's commit after the advisory lock is acquired.
        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('woodshed.operator_stripe_checkout'))"))
    elif dialect == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    else:
        raise ValueError("Unsupported database for operator checkout.")


def authorize(factory, *, adult_id, plan_code, mode, operator, approval):
    """Record the one Finance approval in this database. No provider transport."""
    if type(adult_id) is not int or not 0 < adult_id <= 2_147_483_647:
        raise ValueError("A positive adult verifier ID is required.")
    if not all(isinstance(tag, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", tag)
               for tag in (operator, approval)):
        raise ValueError("Use an internal operator handle and approval reference of 1–64 letters, "
                         "digits, dots, dashes or underscores.")
    config = BillingConfig.from_environment()
    settings = _settings(config, StripeSettings.from_environment(), mode, plan_code)
    plan = new_subscription_plan(plan_code, config)
    binding = dict(adult_verifier_id=adult_id, plan_code=plan_code, mode=mode, operator=operator,
                   approval=approval, stripe_account_id=settings.account_id, price_id=settings.prices[plan_code])
    with factory.begin() as session:
        _lock_authorization(session)
        audit = session.scalars(select(MembershipAuditEvent).where(
            MembershipAuditEvent.action == AUTHORIZATION)).one_or_none()
        if audit is not None:
            if audit.details != binding:
                raise ValueError("This database already has a different operator checkout authorization.")
            attempt = session.get(CheckoutAttempt, audit.checkout_attempt_id)
        else:
            actor = Actor("adult", adult_id)
            account = billing_account(session, actor, create=True)
            at = utc(billing.clock())
            billing.expire_pending_checkouts(session, account.id, at)
            pending = session.scalar(select(CheckoutAttempt.id).where(
                CheckoutAttempt.billing_account_id == account.id, CheckoutAttempt.status == "pending").limit(1))
            if pending is not None:
                raise ValueError("Resolve the adult's pending checkout before authorizing this purchase.")
            attempt = billing.new_checkout_attempt(session, account, actor, "stripe", plan, config, at)
            audit = MembershipAuditEvent(checkout_attempt_id=attempt.id, action=AUTHORIZATION,
                                        actor_type="operator", details=binding)
            session.add(audit)
            session.flush()
        return audit.id, attempt.id, utc(attempt.expires_at)


def _check_binding(config, settings, bound):
    code = bound["plan_code"]
    _settings(config, settings, bound["mode"], code)
    if settings.account_id != bound["stripe_account_id"] or settings.prices[code] != bound["price_id"]:
        raise ValueError("The Stripe account or price differs from the approval.")


def issue(factory, authorization_id):
    """Issue the hosted session privately; give the purchaser a Woodshed review link."""
    if type(authorization_id) is not int or not 0 < authorization_id <= 2_147_483_647:
        raise ValueError("A positive authorization ID is required.")
    config = BillingConfig.from_environment()
    settings = StripeSettings.from_environment()

    def authorized_attempt(session):
        audit = session.scalar(select(MembershipAuditEvent).where(
            MembershipAuditEvent.id == authorization_id, MembershipAuditEvent.action == AUTHORIZATION))
        if audit is None:
            raise ValueError("Operator checkout authorization not found.")
        bound = audit.details
        code = bound["plan_code"]
        _check_binding(config, settings, bound)
        actor = Actor("adult", bound["adult_verifier_id"])
        account = billing_account(session, actor, create=True)
        at = utc(billing.clock())
        billing.expire_pending_checkouts(session, account.id, at)
        attempt = session.get(CheckoutAttempt, audit.checkout_attempt_id, populate_existing=True)
        if attempt is None or attempt.provider != "stripe" or attempt.plan_code != code:
            raise ValueError("The recorded checkout does not match the approval.")
        return billing.reuse_checkout_attempt(session, account, actor, attempt, at)

    billing.start_authorized_checkout(factory, config, authorized_attempt)
    return f"{settings.base_url}/membership/private/{authorization_id}"


def issued_attempt(session, actor, authorization_id, config):
    """Read-only review, limited to the bound adult and a still-open authorization."""
    if actor.kind != "adult":
        raise LookupError("Private checkout unavailable.")
    row = session.execute(select(CheckoutAttempt, MembershipAuditEvent.details)
        .join(MembershipAuditEvent, MembershipAuditEvent.checkout_attempt_id == CheckoutAttempt.id)
        .join(BillingAccount, BillingAccount.id == CheckoutAttempt.billing_account_id)
        .where(MembershipAuditEvent.id == authorization_id, MembershipAuditEvent.action == AUTHORIZATION,
               BillingAccount.verifier_id == actor.id)).one_or_none()
    if row is None:
        raise LookupError("Private checkout unavailable.")
    attempt, bound = row
    if (attempt.status != "pending" or utc(attempt.expires_at) <= utc(billing.clock())
            or not attempt.provider_checkout_id or attempt.provider != "stripe"
            or attempt.plan_code != bound["plan_code"] or actor.id != bound["adult_verifier_id"]):
        raise ValueError("This private checkout is unavailable or has expired.")
    _check_binding(config, StripeSettings.from_environment(), bound)
    return attempt


def continue_checkout(factory, actor, authorization_id, plan_code, terms_version):
    config = BillingConfig.from_environment()
    with factory.begin() as session:
        attempt = issued_attempt(session, actor, authorization_id, config)
        account = lock_billing_account(session, attempt.billing_account_id)
        session.refresh(attempt)
        billing.reuse_checkout_attempt(session, account, actor, attempt, utc(billing.clock()))
        if attempt.plan_code != plan_code:
            raise ValueError("Review the authorized plan before continuing.")
        billing.record_checkout_consent(session, attempt, actor, terms_version)
        checkout_id, reference = attempt.provider_checkout_id, attempt.reference
    provider = billing.enabled_provider("stripe", config)
    return provider.issued_checkout_url(checkout_id, reference, plan_code)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("authorize", help="Record the adult and plan locally")
    prepare.add_argument("--adult-verifier-id", type=int, required=True)
    prepare.add_argument("--plan", choices=tuple(PLAN_ENV), required=True)
    prepare.add_argument("--mode", choices=("test", "live"), required=True)
    prepare.add_argument("--operator", required=True)
    prepare.add_argument("--approval", required=True)
    open_checkout = commands.add_parser("issue", help="Issue the session and get the purchaser's private review link")
    open_checkout.add_argument("--authorization-id", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "authorize":
            audit_id, attempt_id, expires = authorize(
                SessionLocal, adult_id=args.adult_verifier_id, plan_code=args.plan,
                mode=args.mode, operator=args.operator, approval=args.approval)
            print(f"Authorization {audit_id}, attempt {attempt_id}, adult verifier {args.adult_verifier_id}, "
                  f"plan {args.plan}, {args.mode} mode, expires {expires.isoformat()}")
        else:
            print(issue(SessionLocal, args.authorization_id))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Database outcome unknown. Reconcile the original approval before retrying.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
