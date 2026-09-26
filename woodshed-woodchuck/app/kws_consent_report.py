"""Read-only operator inventory of active under-13 accounts and KWS consent flows.

Run with ``python -m app.kws_consent_report --database-url URL`` or an explicitly
set ``DATABASE_URL``. Queries select only the columns needed by the report;
the shared inventory connection enforces a read-only transaction. Runtime KWS
configuration and notice-policy eligibility are not evaluated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import sys

from sqlalchemy import text

from .team_continuity_inventory import InventoryError, readonly_connection, target_url, utc


PROFILE_SQL = text("""
SELECT p.id AS profile_id, p.woodchuck_id, p.display_name, p.status,
       r.age_band, r.consent_id,
       e.id AS evidence_id, e.profile_id AS evidence_profile_id,
       CASE WHEN e.parent_email IS NOT NULL AND e.parent_email <> '' THEN 1 ELSE 0 END AS has_parent_email,
       CASE WHEN e.approved_at IS NOT NULL THEN 1 ELSE 0 END AS has_approval,
       CASE WHEN e.confirmed_at IS NOT NULL THEN 1 ELSE 0 END AS has_confirmation,
       CASE WHEN e.withdrawn_at IS NOT NULL THEN 1 ELSE 0 END AS is_withdrawn,
       CASE WHEN e.notice_version IS NOT NULL AND e.notice_version <> '' THEN 1 ELSE 0 END AS has_notice_version,
       CASE WHEN e.notice_sha256 IS NOT NULL AND e.notice_sha256 <> '' THEN 1 ELSE 0 END AS has_notice_digest
FROM woodchuck_profiles AS p
JOIN account_privacy_rules AS r ON r.profile_id = p.id
LEFT JOIN child_consent_evidence AS e ON e.id = r.consent_id
WHERE p.status = 'active' AND r.age_band = 'under13'
ORDER BY p.id
""")

FLOW_SQL = text("""
SELECT c.profile_id, c.id AS pending_consent_id, c.created_at AS pending_created_at,
       c.expires_at AS pending_expires_at,
       v.id AS kws_verification_id, v.state AS kws_state,
       v.created_at AS kws_created_at, v.completed_at AS kws_completed_at,
       v.activated_consent_id
FROM child_pending_consents AS c
LEFT JOIN child_kws_verifications AS v ON v.pending_id = c.id
WHERE c.profile_id IN (
    SELECT p.id FROM woodchuck_profiles AS p
    JOIN account_privacy_rules AS r ON r.profile_id = p.id
    WHERE p.status = 'active' AND r.age_band = 'under13'
)
ORDER BY c.profile_id, c.created_at DESC, c.id DESC
""")


def _consent_status(row):
    if row["consent_id"] is None:
        return "absent"
    if row["evidence_id"] is None:
        return "missing"
    if row["evidence_profile_id"] != row["profile_id"]:
        return "invalid"
    if row["is_withdrawn"]:
        return "withdrawn"
    if not all(row[key] for key in ("has_parent_email", "has_approval", "has_confirmation",
                                    "has_notice_version", "has_notice_digest")):
        return "incomplete"
    return "complete"


def _classification(flow, now, consent_status):
    if flow is None:
        return "under13_no_flow" if consent_status == "absent" else f"{consent_status}_consent_no_flow"
    state = flow["kws_state"]
    if state == "failed":
        return "kws_failed"
    if state == "cancelled":
        return "kws_cancelled"
    if state == "activated":
        return "activated_without_complete_evidence"
    if utc(flow["pending_expires_at"]) <= now:
        return "pending_expired"
    if state is None:
        return "pending_no_kws"
    if state == "delivery_unknown":
        return "kws_delivery_unknown"
    if state in {"reserved", "accepted"}:
        return "kws_incomplete"
    if state == "verified":
        return "verified_not_activated"
    return "kws_unrecognized_state"


def report(database_url, *, now=None):
    """Inspect a single read-only snapshot; one result per structurally incomplete student.

    The most recently created attempt represents the current flow. The count of
    all attempts is retained so repeated requests are visible without exposing
    older parent details or expanding output into one row per attempt.
    """
    instant = utc(now or datetime.now(timezone.utc))
    with readonly_connection(database_url) as connection:
        profiles = connection.execute(PROFILE_SQL).mappings().all()
        flows = defaultdict(list)
        for flow in connection.execute(FLOW_SQL).mappings():
            flows[flow["profile_id"]].append(flow)

    counts = Counter()
    affected = []
    for profile in profiles:
        consent_status = _consent_status(profile)
        if consent_status == "complete":
            counts["complete_consent_evidence"] += 1
            continue
        counts["without_complete_consent_evidence"] += 1
        attempts = flows[profile["profile_id"]]
        latest = attempts[0] if attempts else None
        reason = _classification(latest, instant, consent_status)
        counts[reason] += 1
        if len(attempts) > 1:
            counts["repeated_attempts"] += 1
        affected.append({
            "profile_id": profile["profile_id"],
            "woodchuck_id": profile["woodchuck_id"],
            "display_name": profile["display_name"],
            "account_status": profile["status"],
            "age_band": profile["age_band"],
            "consent_id": profile["consent_id"],
            "consent_status": consent_status,
            "pending_consent_id": latest["pending_consent_id"] if latest else None,
            "pending_created_at": utc(latest["pending_created_at"]) if latest else None,
            "pending_expires_at": utc(latest["pending_expires_at"]) if latest else None,
            "kws_verification_id": latest["kws_verification_id"] if latest else None,
            "kws_state": latest["kws_state"] if latest else None,
            "kws_created_at": utc(latest["kws_created_at"]) if latest else None,
            "kws_completed_at": utc(latest["kws_completed_at"]) if latest else None,
            "activated_consent_id": latest["activated_consent_id"] if latest else None,
            "pending_attempt_count": len(attempts),
            "classification": reason,
        })
    return {
        "total_under13_accounts_inspected": len(profiles),
        "complete_consent_evidence": counts["complete_consent_evidence"],
        "without_complete_consent_evidence": counts["without_complete_consent_evidence"],
        "runtime_policy_eligibility_evaluated": False,
        "classification_counts": {key: counts[key] for key in sorted(counts)
                                  if key not in {"complete_consent_evidence", "without_complete_consent_evidence",
                                                 "repeated_attempts"}},
        "repeated_attempts": counts["repeated_attempts"],
        "accounts": affected,
    }


def _format(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return "-"
    return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value)


def human_report(result):
    lines = ["READ-ONLY KWS CONSENT REPORT — NO REPAIR PERFORMED",
             f"Active under-13 accounts inspected: {result['total_under13_accounts_inspected']}",
             f"Complete consent evidence: {result['complete_consent_evidence']}",
             f"Without complete consent evidence: {result['without_complete_consent_evidence']}",
             "Runtime-policy eligibility: not evaluated",
             f"Repeated attempts: {result['repeated_attempts']}", "Classifications:"]
    lines += [f"  {code}: {count}" for code, count in result["classification_counts"].items()]
    sections = (
        ("classification", "profile_id", "woodchuck_id", "display_name"),
        ("account_status", "age_band", "consent_id", "consent_status"),
        ("pending_consent_id", "pending_created_at", "pending_expires_at", "pending_attempt_count"),
        ("kws_verification_id", "kws_state", "kws_created_at", "kws_completed_at", "activated_consent_id"),
    )
    for row in result["accounts"]:
        lines.append("")
        for index, fields in enumerate(sections):
            lines.append(("" if index == 0 else "  ") +
                         "  ".join(f"{key}={_format(row[key])}" for key in fields))
    return "\n".join(lines)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid report arguments; use --help. No connection details printed.\n")


def main(argv=None):
    parser = SafeArgumentParser(description="Read-only KWS parental-consent operator report")
    parser.add_argument("--database-url", help="Explicit target URL; alternatively set DATABASE_URL.")
    args = parser.parse_args(argv)
    value = args.database_url or os.environ.get("DATABASE_URL")
    try:
        target_url(value)
        print(human_report(report(value)))
        return 0
    except InventoryError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print("Report failed; verify target, read-only permissions, and schema. Details withheld.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
