"""Explicit, reviewed persistent Team authority cutover. Not a runtime import.

PLAN and VERIFY use verified read-only snapshots. APPLY owns one guarded
transaction and changes only approved authority markers/control/week versions.
No Team, membership, contribution, result, report, or snapshot is created.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text, update
from sqlalchemy.pool import NullPool

from . import team_continuity_inventory as inventory

REVISION = "p20team001"
LEGACY = "legacy_seasonal_v1"
PERSISTENT = "persistent_v1"
CONFIRMATION = "APPLY PERSISTENT TEAM AUTHORITY"
ACKS = ("backup_taken", "writers_paused", "finalization_paused", "maintenance_mode")
TABLES = (
    "seasons", "team_families", "team_name_claims", "teams", "team_memberships",
    "team_join_requests", "team_reports", "woodchuck_profiles", "profile_capabilities",
    "contest_weeks", "practice_charts", "camp_point_awards", "contest_results",
    "team_week_membership_snapshots", "reward_grants", "crown_awards", "crown_progress", "reward_inventory_placements",
    "director_team_contests", "director_team_contest_entries", "director_team_contest_results",
    "persistent_team_control", "team_membership_transitions",
)


class CutoverError(inventory.InventoryError):
    """Deliberately sanitized failures; never expose a URL/driver exception."""


def normalized(value):
    return json.loads(json.dumps(value, default=inventory.json_value, sort_keys=True))


def digest(value):
    encoded = json.dumps(normalized(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _clock(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise CutoverError("aware_time_required")
    return value.astimezone(timezone.utc)


def _ids(values, label):
    result = list(values)
    if any(type(v) is not int or v <= 0 for v in result) or len(set(result)) != len(result):
        raise CutoverError("unique_positive_explicit_ids_required: " + label)
    return sorted(result)


def _schema_guard(connection):
    if list(connection.scalars(text("SELECT version_num FROM alembic_version"))) != [REVISION]:
        raise CutoverError("revision_not_approved: require " + REVISION)
    inspector = inspect(connection)
    if set(TABLES) - set(inspector.get_table_names()):
        raise CutoverError("required_schema_incomplete")
    required_indexes = {
        "teams": {
            "uq_team_operating_family": (["family_id"], "is_operating = true"),
            "uq_team_operating_name": (["normalized_name"], "is_operating = true"),
            "uq_team_operating_emblem": (["emblem_key"], "is_operating = true"),
            "uq_team_operating_public_creator": (["creator_profile_id"], "is_operating = true AND visibility = 'public' AND creator_profile_id IS NOT NULL"),
        },
        "team_memberships": {"uq_team_membership_persistent_active_profile": (["profile_id"], "is_persistent = true AND ended_at IS NULL")},
        "team_join_requests": {"uq_team_join_request_persistent_pending_profile": (["profile_id"], "status = 'pending' AND is_persistent = true")},
    }
    def sql_shape(value):
        # PostgreSQL adds parentheses/casts; conjunction order is immaterial.
        clauses = re.split(r"\band\b", str(value).lower().replace("::text", ""))
        return sorted(re.sub(r"[\s()]", "", clause) for clause in clauses)
    for name, expected in required_indexes.items():
        indexes = {i["name"]: i for i in inspector.get_indexes(name) if i.get("unique")}
        for index_name, (columns, predicate) in expected.items():
            index = indexes.get(index_name)
            if (index is None or index["column_names"] != columns or sql_shape(
                    index.get("dialect_options", {}).get(connection.dialect.name + "_where")) != sql_shape(predicate)):
                raise CutoverError("authority_constraints_missing_or_changed: " + name)
        fks = inspector.get_foreign_keys(name)
        if not any(f["constrained_columns"] == ["season_id"]
                   and f["referred_table"] == "seasons" and f["referred_columns"] == ["id"]
                   and f.get("options", {}).get("ondelete", "").upper() == "RESTRICT" for f in fks):
            raise CutoverError("origin_season_delete_guard_missing: " + name)
    from . import team_authority_schema as guards
    if connection.dialect.name == "postgresql":
        guard = connection.execute(text("""
          SELECT p.prosrc, p.prosecdef, t.tgenabled, t.tgtype
          FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
          WHERE t.tgrelid='team_memberships'::regclass AND NOT t.tgisinternal
            AND t.tgname='persistent_team_membership_interval_guard'
            AND p.proname='enforce_persistent_team_membership_interval'
        """)).mappings().all()
        if (len(guard) != 1 or guard[0]["prosecdef"] or guard[0]["tgenabled"] != "O"
                or guard[0]["tgtype"] != 23
                or guard[0]["prosrc"].strip() != guards.PG_FUNCTION.split("$$")[1].strip()):
            raise CutoverError("persistent_interval_guard_missing_or_changed")
    else:
        found = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='team_memberships'" )).all())
        for trigger_name, expected in (("persistent_team_membership_interval_insert", guards.SQLITE_INSERT),
                                       ("persistent_team_membership_interval_update", guards.SQLITE_UPDATE)):
            if found.get(trigger_name, "").strip() != expected.strip():
                raise CutoverError("persistent_interval_guard_missing_or_changed")


def _tables(connection):
    metadata = MetaData()
    return {name: Table(name, metadata, autoload_with=connection, resolve_fks=False) for name in TABLES}


def _snapshot(connection, tables):
    # Full-column hashes retain private fields only inside the transaction.
    # Export only exact selected authority state and per-table hashes/counts.
    return {name: normalized([dict(r) for r in connection.execute(
        select(table).order_by(*table.primary_key.columns)).mappings()])
            for name, table in tables.items()}


def _evidence(rows):
    return {name: {"count": len(values), "sha256": digest(values)} for name, values in rows.items()}


def _safe_team(row):
    item = dict(row)
    code = item.pop("join_code")
    item["join_code_sha256"] = digest(code) if code else None
    return item


def _target(connection, url):
    target = inventory.target_metadata(inventory.target_url(url))
    if connection.dialect.name == "postgresql":
        target["schema"] = connection.scalar(text("SELECT current_schema()"))
    else:
        target["database"] = str(Path(target["database"]).expanduser().resolve(strict=True))
    return target


def _build(connection, url, team_ids, membership_ids, rules_from_week_start,
           join_request_ids=(), *, now=None):
    _schema_guard(connection)
    moment = _clock(now)
    approved_teams = _ids(team_ids, "teams")
    approved_members = _ids(membership_ids, "memberships")
    approved_requests = _ids(join_request_ids, "requests")
    if not approved_teams:
        raise CutoverError("explicit_operating_teams_required")
    boundary = date.fromisoformat(rules_from_week_start) if isinstance(rules_from_week_start, str) else rules_from_week_start
    if type(boundary) is not date or boundary.weekday() != 0:
        raise CutoverError("clean_monday_rules_boundary_required")
    tables = _tables(connection)
    rows = _snapshot(connection, tables)
    control = rows["persistent_team_control"]
    if control != [{"id": 1, "activated_at": None, "rules_from_week_start": None}]:
        raise CutoverError("disabled_singleton_control_required")
    if (any(t["is_operating"] for t in rows["teams"])
            or any(m["is_persistent"] for m in rows["team_memberships"])
            or any(r["is_persistent"] for r in rows["team_join_requests"])
            or rows["team_membership_transitions"]):
        raise CutoverError("persistent_authority_already_present")
    teams = [t for t in rows["teams"] if t["id"] in approved_teams]
    members = [m for m in rows["team_memberships"] if m["id"] in approved_members]
    requests = [r for r in rows["team_join_requests"] if r["id"] in approved_requests]
    if len(teams) != len(approved_teams) or len(members) != len(approved_members) or len(requests) != len(approved_requests):
        raise CutoverError("approved_id_missing")
    families = [t["family_id"] for t in teams]
    if len(set(families)) != len(families):
        raise CutoverError("multiple_operating_teams_in_family")
    for field in ("normalized_name", "emblem_key"):
        if len({t[field] for t in teams}) != len(teams):
            raise CutoverError("operating_identity_collision: " + field)
    creators = [t["creator_profile_id"] for t in teams if t["visibility"] == "public" and t["creator_profile_id"] is not None]
    if len(set(creators)) != len(creators):
        raise CutoverError("operating_public_creator_collision")
    claims = {r["normalized_name"]: r["family_id"] for r in rows["team_name_claims"]}
    profiles = {p["id"]: p for p in rows["woodchuck_profiles"]}
    capabilities = {(c["profile_id"], c["capability"]) for c in rows["profile_capabilities"]}
    for team in teams:
        if team["moderation_status"] not in {"active", "under_review", "hidden"}:
            raise CutoverError("invalid_moderation_state")
        if team["visibility"] == "public" and claims.get(team["normalized_name"]) != team["family_id"]:
            raise CutoverError("permanent_public_name_claim_mismatch")
        owner = team["creator_profile_id"]
        if owner is not None and profiles[owner]["status"] != "active":
            raise CutoverError("inactive_team_creator_requires_review")
        if team["director_led"] and ((owner, "band_director") not in capabilities
                                      or team["visibility"] != "private" or not team["join_code"]):
            raise CutoverError("director_authority_requires_review")
    current_member_ids = {m["id"] for m in rows["team_memberships"] if m["team_id"] in approved_teams
                          and m["ended_at"] is None}
    if current_member_ids != set(approved_members):
        raise CutoverError("exact_unended_operating_memberships_required")
    member_profiles = [m["profile_id"] for m in members]
    if len(set(member_profiles)) != len(member_profiles):
        raise CutoverError("multiple_persistent_memberships_for_profile")
    for member in members:
        if inventory.utc(member["started_at"]) > moment or profiles[member["profile_id"]]["status"] != "active":
            raise CutoverError("membership_not_effective_or_profile_inactive")
    pending_ids = {r["id"] for r in rows["team_join_requests"] if r["status"] == "pending"}
    if pending_ids != set(approved_requests):
        raise CutoverError("every_pending_request_requires_explicit_review")
    if len({r["profile_id"] for r in requests}) != len(requests):
        raise CutoverError("multiple_pending_requests_for_profile")
    if any(r["team_id"] not in approved_teams or profiles[r["profile_id"]]["status"] != "active" for r in requests):
        raise CutoverError("pending_request_not_operating_or_profile_inactive")
    today = moment.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    covering = [w for w in rows["contest_weeks"] if w["week_start"] <= today < w["week_end"]]
    if len(covering) != 1 or covering[0]["status"] != "open" or covering[0]["finalized_at"] is not None:
        raise CutoverError("unique_open_current_week_required")
    current_start = date.fromisoformat(covering[0]["week_start"])
    current_boundary = datetime.combine(current_start, time.min,
                                        ZoneInfo("America/Chicago")).astimezone(timezone.utc)
    # Promotion must not erase a legacy choice already consumed this week.
    # No ledger backfill is authorized: wait for a clean later week instead.
    if any(inventory.utc(m["started_at"]) >= current_boundary
           or m["selected_week_start"] >= current_start.isoformat() for m in members):
        raise CutoverError("approved_memberships_not_carried_from_before_current_week")
    if any(m["selected_week_start"] == current_start.isoformat()
           or current_boundary <= inventory.utc(m["started_at"]) <= moment
           or (m["ended_at"] is not None
               and current_boundary <= inventory.utc(m["ended_at"]) <= moment)
           for m in rows["team_memberships"]):
        raise CutoverError("legacy_current_week_transition_requires_clean_later_week")
    if boundary.isoformat() < covering[0]["week_end"]:
        raise CutoverError("rules_boundary_must_follow_current_week")
    if any(w["week_start"] < boundary.isoformat() < w["week_end"] for w in rows["contest_weeks"]):
        raise CutoverError("rules_boundary_intersects_existing_week")
    for a in rows["contest_weeks"]:
        if a["team_membership_rules_version"] != LEGACY:
            raise CutoverError("unexpected_existing_week_rules")
        if any(a["id"] < b["id"] and a["week_start"] < b["week_end"] and b["week_start"] < a["week_end"]
               for b in rows["contest_weeks"]):
            raise CutoverError("overlapping_contest_weeks")
    future = [w for w in rows["contest_weeks"] if w["week_start"] >= boundary.isoformat()]
    for week in [covering[0], *future]:
        start = date.fromisoformat(week["week_start"])
        if start.weekday() != 0 or date.fromisoformat(week["week_end"]) != start + timedelta(days=7):
            raise CutoverError("current_or_prospective_week_not_clean_calendar_week")
    future_ids = {w["id"] for w in future}
    if any(w["status"] != "open" or w["finalized_at"] is not None for w in future):
        raise CutoverError("prospective_weeks_not_clean_open")
    if any(r["contest_week_id"] in future_ids for n in ("contest_results", "team_week_membership_snapshots") for r in rows[n]):
        raise CutoverError("prospective_week_has_frozen_evidence")
    if any(c["status"] != "finalized" for c in rows["director_team_contests"]):
        raise CutoverError("nonfinalized_director_contest_requires_separate_review")
    content = {
        "revision": REVISION, "target": _target(connection, url),
        "team_ids": approved_teams, "membership_ids": approved_members, "join_request_ids": approved_requests,
        "rules_from_week_start": boundary.isoformat(), "prospective_week_ids": sorted(future_ids),
        "current_legacy_week_id": covering[0]["id"],
        "operating_teams": [_safe_team(t) for t in teams], "persistent_memberships": members,
        "persistent_join_requests": requests,
        "legacy_unended_membership_ids": [m["id"] for m in rows["team_memberships"]
                                             if m["ended_at"] is None and m["id"] not in approved_members],
        "before": _evidence(rows),
        "allowed_changes": {"teams": "is_operating", "team_memberships": "is_persistent",
                            "team_join_requests": "is_persistent", "contest_weeks": "team_membership_rules_version",
                            "persistent_team_control": "activated_at,rules_from_week_start"},
    }
    return {"content": content, "plan_sha256": digest(content),
            "metadata": {"generated_at": moment.isoformat()}}, rows, tables


def generate_plan(url, team_ids, membership_ids, rules_from_week_start, *, join_request_ids=(), now=None):
    """Read-only preflight. Caller supplies IDs; no runtime inference is used."""
    with inventory.readonly_connection(url) as connection:
        return _build(connection, url, team_ids, membership_ids, rules_from_week_start,
                      join_request_ids, now=now)[0]


def _approved(plan, expected_sha256):
    if (not isinstance(expected_sha256, str) or not isinstance(plan, dict)
            or not hmac.compare_digest(digest(plan.get("content")), expected_sha256)
            or plan.get("plan_sha256") != expected_sha256):
        raise CutoverError("approved_plan_hash_mismatch")
    return plan["content"]


def _expected_after(before, content, moment):
    after = normalized(before)
    for name, ids, marker in (("teams", content["team_ids"], "is_operating"),
                              ("team_memberships", content["membership_ids"], "is_persistent"),
                              ("team_join_requests", content["join_request_ids"], "is_persistent")):
        for row in after[name]:
            if row["id"] in ids:
                row[marker] = True
    for row in after["contest_weeks"]:
        if row["id"] in content["prospective_week_ids"]:
            row["team_membership_rules_version"] = PERSISTENT
    after["persistent_team_control"] = [{"id": 1, "activated_at": normalized(moment),
                                          "rules_from_week_start": content["rules_from_week_start"]}]
    return after


def apply_cutover(url, plan, expected_sha256, *, acknowledgments, confirmation, now=None):
    """Only an explicitly approved manifest can authorize this standalone write."""
    content = _approved(plan, expected_sha256)
    if confirmation != CONFIRMATION or any(acknowledgments.get(ack) is not True for ack in ACKS):
        raise CutoverError("explicit_operator_confirmation_and_maintenance_acknowledgments_required")
    target = inventory.target_url(url)
    if target.get_backend_name() == "sqlite":
        Path(target.database).expanduser().resolve(strict=True)
    engine = create_engine(target, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
                # Lock ordering starts with the same singleton used by runtime
                # writers; broad locks also protect the full history assertion.
                connection.execute(text("SELECT id FROM persistent_team_control WHERE id=1 FOR UPDATE"))
                connection.execute(text("LOCK TABLE " + ", ".join(TABLES) + " IN SHARE ROW EXCLUSIVE MODE"))
            try:
                # Waiting writers/operator transactions sample after the fence.
                moment = _clock(now)
                fresh, before, tables = _build(connection, url, content["team_ids"], content["membership_ids"],
                                                content["rules_from_week_start"], content["join_request_ids"], now=moment)
                if not hmac.compare_digest(fresh["plan_sha256"], expected_sha256):
                    raise CutoverError("stale_plan: regenerate_and_review")
                for name, ids, marker in (("teams", content["team_ids"], "is_operating"),
                                          ("team_memberships", content["membership_ids"], "is_persistent"),
                                          ("team_join_requests", content["join_request_ids"], "is_persistent")):
                    if ids:
                        connection.execute(update(tables[name]).where(tables[name].c.id.in_(ids)).values({marker: True}))
                if content["prospective_week_ids"]:
                    connection.execute(update(tables["contest_weeks"]).where(
                        tables["contest_weeks"].c.id.in_(content["prospective_week_ids"])).values(team_membership_rules_version=PERSISTENT))
                connection.execute(update(tables["persistent_team_control"]).where(
                    tables["persistent_team_control"].c.id == 1).values(activated_at=moment,
                                    rules_from_week_start=date.fromisoformat(content["rules_from_week_start"])))
                after = _snapshot(connection, tables)
                expected = _expected_after(before, content, moment)
                if after != expected:
                    raise CutoverError("unexpected_row_change: transaction_rolled_back")
                verification = {"passed": True, "before": _evidence(before), "after": _evidence(after),
                                "historical_attribution_unchanged": True, "new_teams": 0, "new_memberships": 0,
                                "activated_at": moment.isoformat(), "approved_plan_sha256": expected_sha256}
                connection.commit()
                return {"transaction_state": "committed", "verification": verification}
            except Exception:
                connection.rollback()
                raise
    except (CutoverError, inventory.InventoryError):
        raise
    except Exception:
        raise CutoverError("database_operation_failed: transaction_rolled_back; details_withheld") from None
    finally:
        engine.dispose()


def verify_cutover(url, plan, expected_sha256, receipt):
    """Read-only immediate post-apply check against exact approved evidence."""
    content = _approved(plan, expected_sha256)
    if receipt.get("verification", {}).get("approved_plan_sha256") != expected_sha256:
        raise CutoverError("approved_receipt_required")
    with inventory.readonly_connection(url) as connection:
        _schema_guard(connection)
        if _target(connection, url) != content["target"]:
            raise CutoverError("target_mismatch")
        evidence = _evidence(_snapshot(connection, _tables(connection)))
        if evidence != receipt["verification"]["after"]:
            raise CutoverError("post_apply_state_changed: do_not_reapply")
        return {"passed": True, "after": evidence}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "apply", "verify"))
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument("--team-ids", nargs="+", type=int)
    parser.add_argument("--membership-ids", nargs="*", type=int, default=[])
    parser.add_argument("--join-request-ids", nargs="*", type=int, default=[])
    parser.add_argument("--rules-from-week-start")
    parser.add_argument("--plan-file")
    parser.add_argument("--approved-sha256")
    parser.add_argument("--receipt-file")
    parser.add_argument("--confirmation")
    for ack in ACKS:
        parser.add_argument("--" + ack.replace("_", "-"), action="store_true")
    args = parser.parse_args(argv)
    try:
        url = os.getenv(args.database_url_env)
        if not url:
            raise CutoverError("explicit_database_url_environment_required")
        if args.operation == "plan":
            if not args.team_ids or not args.rules_from_week_start:
                raise CutoverError("explicit_ids_and_rules_boundary_required")
            result = generate_plan(url, args.team_ids, args.membership_ids,
                                   args.rules_from_week_start, join_request_ids=args.join_request_ids)
        else:
            if not args.plan_file or not args.approved_sha256:
                raise CutoverError("approved_plan_file_and_sha256_required")
            plan = json.loads(Path(args.plan_file).read_text())
            if args.operation == "apply":
                result = apply_cutover(url, plan, args.approved_sha256,
                            acknowledgments={ack: getattr(args, ack) for ack in ACKS}, confirmation=args.confirmation)
            else:
                if not args.receipt_file:
                    raise CutoverError("approved_receipt_required")
                result = verify_cutover(url, plan, args.approved_sha256,
                                        json.loads(Path(args.receipt_file).read_text()))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (CutoverError, inventory.InventoryError) as exc:
        print(json.dumps({"refused": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"refused": "operation_failed: private_details_withheld"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
