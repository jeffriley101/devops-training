"""Delete expired, non-activated KWS flows after the approved retention delay."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math

from sqlalchemy import and_, or_, select, text

from .child_models import PendingConsent
from .db import SessionLocal
from .kws_models import KWSEmailBudget, KWSVerification


RETENTION_DELAY = timedelta(days=30)
EMAIL_BUDGET_WINDOW = timedelta(hours=1)
REMOVABLE_STATES = {
    "reserved", "accepted", "delivery_unknown", "verified", "failed", "cancelled",
}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _flow_expired(row: PendingConsent, verification: KWSVerification | None,
                  cutoff: datetime) -> bool:
    """Fail closed for unknown/corrupt states and every activated verification."""
    if verification is None:
        return utc(row.expires_at) <= cutoff
    if verification.state == "activated" or verification.activated_consent_id is not None:
        return False
    if verification.state not in REMOVABLE_STATES:
        return False
    if verification.state == "failed":
        return bool(verification.completed_at and utc(verification.completed_at) <= cutoff)
    return utc(row.expires_at) <= cutoff


def _valid_budget_history(value) -> bool:
    return (isinstance(value, list)
            and all(type(item) in (int, float) and math.isfinite(item) for item in value))


def cleanup(session, *, now: datetime | None = None, apply: bool = False) -> dict[str, int]:
    """Count or remove eligible flows and stale email-budget history.

    The caller owns the transaction. A dry run does not mutate ORM objects.
    """
    now = utc(now or datetime.now(timezone.utc))
    cutoff = now - RETENTION_DELAY
    budget_cutoff = now.timestamp() - EMAIL_BUDGET_WINDOW.total_seconds()
    counts = {
        "pending_consents": 0,
        "kws_verifications": 0,
        "email_budget_rows": 0,
        "email_budget_timestamps": 0,
    }

    # The broad predicate keeps the job bounded while the locked recheck below is
    # authoritative. Failed results become unusable at completion; other flows use
    # the pending request/activation expiry.
    candidate_ids = session.scalars(
        select(PendingConsent.id)
        .outerjoin(KWSVerification, KWSVerification.pending_id == PendingConsent.id)
        .where(or_(
            PendingConsent.expires_at <= cutoff,
            and_(KWSVerification.state == "failed",
                 KWSVerification.completed_at.is_not(None),
                 KWSVerification.completed_at <= cutoff),
        ))
        .order_by(PendingConsent.id)
    ).all()
    for pending_id in candidate_ids:
        row = session.scalar(
            select(PendingConsent).where(PendingConsent.id == pending_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if row is None:
            continue
        verification = session.scalar(
            select(KWSVerification).where(KWSVerification.pending_id == pending_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if not _flow_expired(row, verification, cutoff):
            continue
        counts["pending_consents"] += 1
        counts["kws_verifications"] += int(verification is not None)
        if apply:
            if verification is not None:
                session.delete(verification)
                session.flush()
            session.delete(row)

    budgets = session.scalars(
        select(KWSEmailBudget).order_by(KWSEmailBudget.email_hash).with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for budget in budgets:
        # Unexpected data is retained rather than risking a weaker limiter.
        if not _valid_budget_history(budget.sent_at):
            continue
        current = [stamp for stamp in budget.sent_at if stamp > budget_cutoff]
        removed = len(budget.sent_at) - len(current)
        if not removed:
            continue
        counts["email_budget_rows"] += 1
        counts["email_budget_timestamps"] += removed
        if apply:
            if current:
                budget.sent_at = current
            else:
                session.delete(budget)

    if apply:
        session.flush()
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Count eligible records only")
    mode.add_argument("--apply", action="store_true", help="Delete eligible records atomically")
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        try:
            if args.apply and session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            report = cleanup(session, apply=args.apply)
            if args.apply:
                session.commit()
            else:
                session.rollback()
            print(json.dumps(report, sort_keys=True))
            return 0
        except Exception:
            session.rollback()
            # Scheduled-job output remains counts-only and never includes row data.
            print(json.dumps({"errors": 1}))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
