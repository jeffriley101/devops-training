"""Explicit, reviewed persistent Team authority cutover. Not a runtime import.

PLAN and VERIFY are read-only. APPLY stages an exact approval without promoting
any authority. ACTIVATE freezes the closing legacy roster and promotes approved
authority and weekly rules together, behind the runtime serialization fence.
No Team, membership, contribution, result, or report is created or repaired.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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
from . import operator_schema_compatibility as compatibility

# Immutable PTA contract; the installed schema may include the c22 extension.
REVISION = "p21team001"
LEGACY = "legacy_seasonal_v1"
PERSISTENT = "persistent_v1"
CONFIRMATION = "STAGE PERSISTENT TEAM AUTHORITY"
ACTIVATION_CONFIRMATION = "ACTIVATE PERSISTENT TEAM AUTHORITY"
ACKS = ("backup_taken", "writers_paused", "finalization_paused", "maintenance_mode")
TABLES = (
    "seasons", "team_families", "team_name_claims", "teams", "team_memberships",
    "team_join_requests", "team_reports", "woodchuck_profiles", "profile_capabilities",
    "contest_weeks", "practice_charts", "camp_point_awards", "contest_results",
    "team_week_membership_snapshots", "reward_grants", "crown_awards", "crown_progress", "reward_inventory_placements",
    "director_team_contests", "director_team_contest_entries", "director_team_contest_results",
    "persistent_team_control", "team_membership_transitions",
)
# Authority and contest writers serialize through the singleton. Do not take
# table locks on ordinary practice, profile, or economy records: those writers
# can hold a profile row needed by the frozen roster's foreign keys. The full
# before/after evidence below still rejects unexpected concurrent changes.
TABLE_LOCKS = tuple(name for name in TABLES if name not in {
    "practice_charts", "camp_point_awards", "woodchuck_profiles", "reward_grants",
    "crown_awards", "crown_progress", "reward_inventory_placements",
})


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
    revision = compatibility.installed_revision(connection, CutoverError)
    inspector = inspect(connection)
    if set(TABLES) - set(inspector.get_table_names()):
        raise CutoverError("required_schema_incomplete")
    required_columns = {
        "persistent_team_control": {"staged_plan", "staged_plan_sha256", "staged_for"},
        "contest_weeks": {"team_roster_frozen_at"},
    }
    for name, columns in required_columns.items():
        if columns - {c["name"] for c in inspector.get_columns(name)}:
            raise CutoverError("staged_boundary_schema_incomplete")
    checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints("persistent_team_control")}
    staging_check = ("(staged_for IS NULL AND staged_plan IS NULL AND staged_plan_sha256 IS NULL) OR "
                     "(staged_for IS NOT NULL AND staged_plan IS NOT NULL AND staged_plan_sha256 IS NOT NULL)")
    if re.sub(r"[\s()]", "", checks.get("ck_persistent_team_control_staging", "").lower()) != re.sub(
            r"[\s()]", "", staging_check.lower()):
        raise CutoverError("staged_boundary_constraint_missing_or_changed")
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
          SELECT p.prosrc, p.prosecdef, p.provolatile, t.tgenabled, t.tgtype
          FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
          WHERE t.tgrelid='team_memberships'::regclass AND NOT t.tgisinternal
            AND t.tgname='persistent_team_membership_interval_guard'
            AND p.proname='enforce_persistent_team_membership_interval'
        """)).mappings().all()
        if (len(guard) != 1 or guard[0]["prosecdef"] or guard[0]["provolatile"] != "v" or guard[0]["tgenabled"] != "O"
                or guard[0]["tgtype"] != 23
                or guard[0]["prosrc"].strip() != guards.PG_FUNCTION.split("$$")[1].strip()):
            raise CutoverError("persistent_interval_guard_missing_or_changed")
    else:
        found = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='team_memberships'" )).all())
        for trigger_name, expected in (("persistent_team_membership_interval_insert", guards.SQLITE_INSERT),
                                       ("persistent_team_membership_interval_update", guards.SQLITE_UPDATE)):
            if found.get(trigger_name, "").strip() != expected.strip():
                raise CutoverError("persistent_interval_guard_missing_or_changed")
    return revision


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
    revision = _schema_guard(connection)
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
    if (len(control) != 1 or control[0]["id"] != 1 or control[0]["activated_at"] is not None
            or control[0]["rules_from_week_start"] is not None):
        raise CutoverError("disabled_singleton_control_required")
    control = control[0]
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
    effective_at = _boundary(boundary)
    # An explicit replacement approval can recover a stale staged plan while
    # the boundary gate is closed. No new or inferred membership is approved.
    replacing_at_boundary = (control["staged_for"] is not None
        and inventory.utc(control["staged_for"]) == effective_at
        and effective_at <= moment < _boundary(boundary + timedelta(days=7)))
    if boundary.isoformat() < covering[0]["week_end"] and not replacing_at_boundary:
        raise CutoverError("rules_boundary_must_follow_current_week")
    closing = [w for w in rows["contest_weeks"] if w["week_end"] == boundary.isoformat()]
    starting = [w for w in rows["contest_weeks"] if w["week_start"] == boundary.isoformat()]
    if len(closing) != 1 or len(starting) != 1:
        raise CutoverError("exact_closing_and_starting_weeks_required")
    if any(inventory.utc(m["started_at"]) >= effective_at for m in members):
        raise CutoverError("approved_memberships_must_precede_boundary")
    if any(w["week_start"] < boundary.isoformat() < w["week_end"] for w in rows["contest_weeks"]):
        raise CutoverError("rules_boundary_intersects_existing_week")
    for a in rows["contest_weeks"]:
        if a["team_membership_rules_version"] != LEGACY:
            raise CutoverError("unexpected_existing_week_rules")
        if any(a["id"] < b["id"] and a["week_start"] < b["week_end"] and b["week_start"] < a["week_end"]
               for b in rows["contest_weeks"]):
            raise CutoverError("overlapping_contest_weeks")
    future = [w for w in rows["contest_weeks"] if w["week_start"] >= boundary.isoformat()]
    for week in [closing[0], *future]:
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
        "revision": revision, "target": _target(connection, url),
        "team_ids": approved_teams, "membership_ids": approved_members, "join_request_ids": approved_requests,
        "rules_from_week_start": boundary.isoformat(), "prospective_week_ids": sorted(future_ids),
        "current_legacy_week_id": closing[0]["id"],
        "effective_at": effective_at.isoformat(),
        "supersedes_staged_plan_sha256": control["staged_plan_sha256"],
        "authority_sha256": _authority_fingerprint(rows),
        "operating_teams": [_safe_team(t) for t in teams], "persistent_memberships": members,
        "persistent_join_requests": requests,
        "legacy_unended_membership_ids": [m["id"] for m in rows["team_memberships"]
                                             if m["ended_at"] is None and m["id"] not in approved_members],
        "before": _evidence(rows),
        "allowed_changes": {"teams": "is_operating", "team_memberships": "is_persistent",
                            "team_join_requests": "is_persistent",
                            "contest_weeks": "team_membership_rules_version,closing_legacy_team_roster_frozen_at",
                            "persistent_team_control": "staged_plan,staged_plan_sha256,staged_for,activated_at,rules_from_week_start",
                            "team_week_membership_snapshots": "closing legacy roster only"},
    }
    if revision != REVISION:
        content["pta_contract_revision"] = REVISION
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
    content = plan["content"]
    if (not isinstance(content, dict) or content.get("revision") not in compatibility.SUPPORTED_REVISIONS
            or content.get("pta_contract_revision", content.get("revision")) != REVISION):
        raise CutoverError("approved_plan_contract_not_supported")
    return content


def _plan_schema_guard(content, revision, *, staging=False):
    # A staged p21 approval and receipt survive the reviewed additive extension.
    # An unstaged approval must still match a fresh plan exactly; never rewrite
    # its revision/hash/evidence to make an APPLY succeed on another schema.
    if content["revision"] != revision and (staging or revision != "c22class001"):
        raise CutoverError("plan_schema_revision_changed: generate_and_review_new_plan_before_apply")


def _boundary(day):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return datetime.combine(day, time.min, ZoneInfo("America/Chicago")).astimezone(timezone.utc)


def _authority_fingerprint(rows):
    """Allow ordinary activity while requiring a fresh approval for authority changes.

    Profile score counters and chart/award/result activity are deliberately absent.
    Every identity, membership, request, moderation report, owner status, and role
    remains part of the approval; IDs are never selected at activation time.
    """
    state = {name: rows[name] for name in (
        "team_families", "team_name_claims", "teams", "team_memberships",
        "team_join_requests", "team_reports", "profile_capabilities",
        "team_membership_transitions", "director_team_contests",
        "director_team_contest_entries", "director_team_contest_results",
    )}
    related_profiles = {m["profile_id"] for m in rows["team_memberships"]}
    related_profiles.update(r["profile_id"] for r in rows["team_join_requests"])
    related_profiles.update(t["creator_profile_id"] for t in rows["teams"] if t["creator_profile_id"] is not None)
    state["profiles"] = [{"id": p["id"], "status": p["status"]}
                         for p in rows["woodchuck_profiles"] if p["id"] in related_profiles]
    # Legacy current authority depends on the durable calendar as well as the
    # membership rows. Keep presentation names and audit timestamps out of it.
    state["seasons"] = [{key: season[key] for key in (
        "id", "key", "starts_on", "ends_on", "timezone", "status")}
        for season in rows["seasons"]]
    state["weeks"] = [{key: w[key] for key in (
        "id", "season_id", "week_start", "week_end", "team_membership_rules_version")}
        for w in rows["contest_weeks"]]
    return digest(state)


def _expected_stage(before, plan, expected_sha256):
    after = normalized(before)
    after["persistent_team_control"][0].update(
        staged_plan=normalized(plan), staged_plan_sha256=expected_sha256,
        staged_for=normalized(_boundary(plan["content"]["rules_from_week_start"])))
    return after


@contextmanager
def _write_connection(url):
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
                # A snapshot taken before waiting on the singleton could miss
                # the writer that held it. Check before any authority read,
                # including activations with no memberships to promote.
                if connection.connection.driver_connection.autocommit:
                    raise CutoverError("persistent_team_cutover_requires_transactional_connection")
                if connection.get_isolation_level() != "READ COMMITTED":
                    raise CutoverError("persistent_team_cutover_requires_read_committed")
                connection.begin()
                # Deliberately raw: runtime's due-boundary gate must not prevent
                # this separately authorized transaction from completing cutover.
                connection.execute(text("SELECT id FROM persistent_team_control WHERE id=1 FOR UPDATE"))
                connection.execute(text("LOCK TABLE " + ", ".join(TABLE_LOCKS) + " IN SHARE ROW EXCLUSIVE MODE"))
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except (CutoverError, inventory.InventoryError):
        raise
    except Exception:
        raise CutoverError("database_operation_failed: transaction_rolled_back; details_withheld") from None
    finally:
        engine.dispose()


def _acknowledged(acknowledgments, confirmation, required):
    if confirmation != required or any(acknowledgments.get(ack) is not True for ack in ACKS):
        raise CutoverError("explicit_operator_confirmation_and_maintenance_acknowledgments_required")


def apply_cutover(url, plan, expected_sha256, *, acknowledgments, confirmation, now=None):
    """Stage only an exact reviewed approval; current operation stays legacy."""
    content = _approved(plan, expected_sha256)
    _acknowledged(acknowledgments, confirmation, CONFIRMATION)
    with _write_connection(url) as connection:
        moment = _clock(now)
        revision = _schema_guard(connection)
        _plan_schema_guard(content, revision, staging=True)
        classroom_before = compatibility.classroom_snapshot(connection, revision)
        fresh, before, tables = _build(connection, url, content["team_ids"], content["membership_ids"],
            content["rules_from_week_start"], content["join_request_ids"], now=moment)
        if not hmac.compare_digest(fresh["plan_sha256"], expected_sha256):
            raise CutoverError("stale_plan: regenerate_and_review")
        connection.execute(update(tables["persistent_team_control"]).where(
            tables["persistent_team_control"].c.id == 1).values(
                staged_plan=normalized(plan), staged_plan_sha256=expected_sha256,
                staged_for=_boundary(content["rules_from_week_start"])))
        after = _snapshot(connection, tables)
        if after != _expected_stage(before, plan, expected_sha256):
            raise CutoverError("unexpected_row_change: transaction_rolled_back")
        compatibility.assert_classroom_unchanged(connection, revision, classroom_before, CutoverError)
        receipt = {"transaction_state": "committed", "operation": "staged",
            "verification": {"passed": True, "before": _evidence(before), "after": _evidence(after),
                "approved_plan_sha256": expected_sha256, "effective_at": content["effective_at"],
                "persistent_authority_active": False}}
    return receipt


def _activation_state(rows, content, expected_sha256, moment):
    control = rows["persistent_team_control"]
    if len(control) != 1 or control[0]["id"] != 1 or control[0]["activated_at"] is not None:
        raise CutoverError("disabled_staged_control_required")
    control = control[0]
    boundary = _boundary(content["rules_from_week_start"])
    if (control["staged_plan_sha256"] != expected_sha256
            or control["staged_plan"] is None
            or _approved(control["staged_plan"], expected_sha256) != content
            or inventory.utc(control["staged_for"]) != boundary):
        raise CutoverError("exact_staged_approval_required")
    if moment < boundary:
        raise CutoverError("effective_boundary_not_reached")
    if moment >= _boundary(date.fromisoformat(content["rules_from_week_start"]) + timedelta(days=7)):
        raise CutoverError("staged_boundary_expired: review_new_boundary")
    if _authority_fingerprint(rows) != content["authority_sha256"]:
        raise CutoverError("staged_authority_changed: regenerate_and_review")
    # A late operator may activate the logical boundary only while the runtime
    # gate preserved an empty new competition interval. Never backdate over work.
    if (any((c["include_contests"] or c["include_team_contests"])
            and (inventory.utc(c["created_at"]) >= boundary
                 or c["practice_date"] >= content["rules_from_week_start"])
            for c in rows["practice_charts"])
            or any(inventory.utc(a["occurred_at"]) >= boundary for a in rows["camp_point_awards"])):
        raise CutoverError("post_boundary_activity_requires_review")
    weeks = {w["id"]: w for w in rows["contest_weeks"]}
    closing = weeks.get(content["current_legacy_week_id"])
    if closing is None or closing["week_end"] != content["rules_from_week_start"]:
        raise CutoverError("closing_legacy_week_changed")
    if closing["team_membership_rules_version"] != LEGACY:
        raise CutoverError("closing_week_must_remain_legacy")
    future = [w for w in rows["contest_weeks"] if w["week_start"] >= content["rules_from_week_start"]]
    if sorted(w["id"] for w in future) != content["prospective_week_ids"]:
        raise CutoverError("prospective_week_set_changed")
    if (not any(w["week_start"] == content["rules_from_week_start"] for w in future)
            or any(w["status"] != "open" or w["finalized_at"] is not None
                   or w["team_membership_rules_version"] != LEGACY
                   or w["team_roster_frozen_at"] is not None for w in future)):
        raise CutoverError("prospective_weeks_not_clean_open")
    future_ids = set(content["prospective_week_ids"])
    if any(r["contest_week_id"] in future_ids for name in (
            "contest_results", "team_week_membership_snapshots") for r in rows[name]):
        raise CutoverError("prospective_week_has_frozen_evidence")
    return boundary, closing


def _closing_roster(rows, closing, boundary):
    """Preserve the legacy exact-end reader, including its half-open intervals."""
    chosen = {}
    members = sorted(rows["team_memberships"], key=lambda m: (
        m["profile_id"], inventory.utc(m["started_at"])), reverse=True)
    for member in members:
        if (member["season_id"] == closing["season_id"]
                and inventory.utc(member["started_at"]) <= boundary
                and (member["ended_at"] is None or boundary < inventory.utc(member["ended_at"]))):
            chosen.setdefault(member["profile_id"], member)
    return [{"contest_week_id": closing["id"], "profile_id": m["profile_id"],
             "team_id": m["team_id"], "membership_id": m["id"], "snapshot_at": normalized(boundary)}
            for _, m in sorted(chosen.items())]


def _freeze_closing_roster(connection, tables, before, expected, closing, boundary, moment):
    if closing["status"] == "finalized" or closing["finalized_at"] is not None:
        # Already-finalized evidence is immutable, including empty rosters.
        return
    if closing["status"] != "open" or closing["team_roster_frozen_at"] is not None:
        raise CutoverError("closing_legacy_week_not_unfrozen_open")
    roster = _closing_roster(before, closing, boundary)
    existing = [s for s in before["team_week_membership_snapshots"] if s["contest_week_id"] == closing["id"]]
    if existing:
        actual = sorted([{key: s[key] for key in roster[0]} for s in existing],
                        key=lambda s: s["profile_id"]) if roster else []
        if not roster or actual != roster:
            raise CutoverError("closing_legacy_snapshot_mismatch")
    else:
        for item in roster:
            values = dict(item, snapshot_at=boundary, created_at=moment)
            inserted = connection.execute(tables["team_week_membership_snapshots"].insert().values(**values))
            expected["team_week_membership_snapshots"].append(normalized(dict(
                values, id=inserted.inserted_primary_key[0])))
        expected["team_week_membership_snapshots"].sort(key=lambda s: s["id"])
    connection.execute(update(tables["contest_weeks"]).where(
        tables["contest_weeks"].c.id == closing["id"]).values(team_roster_frozen_at=boundary))
    next(w for w in expected["contest_weeks"] if w["id"] == closing["id"])["team_roster_frozen_at"] = normalized(boundary)


def activate_cutover(url, plan, expected_sha256, *, acknowledgments, confirmation, now=None):
    """Activate all authorities at one logical Monday boundary, atomically.

    Runtime operations are gated from that boundary until this transaction commits.
    Nothing is inferred if an approved membership changed since staging.
    """
    content = _approved(plan, expected_sha256)
    _acknowledged(acknowledgments, confirmation, ACTIVATION_CONFIRMATION)
    with _write_connection(url) as connection:
        moment = _clock(now)
        revision = _schema_guard(connection)
        _plan_schema_guard(content, revision)
        if _target(connection, url) != content["target"]:
            raise CutoverError("target_mismatch")
        tables = _tables(connection)
        before = _snapshot(connection, tables)
        classroom_before = compatibility.classroom_snapshot(connection, revision)
        boundary, closing = _activation_state(before, content, expected_sha256, moment)
        expected = normalized(before)
        _freeze_closing_roster(connection, tables, before, expected, closing, boundary, moment)
        for name, ids, marker in (("teams", content["team_ids"], "is_operating"),
                                  ("team_memberships", content["membership_ids"], "is_persistent"),
                                  ("team_join_requests", content["join_request_ids"], "is_persistent")):
            if ids:
                connection.execute(update(tables[name]).where(tables[name].c.id.in_(ids)).values({marker: True}))
            for row in expected[name]:
                if row["id"] in ids:
                    row[marker] = True
        connection.execute(update(tables["contest_weeks"]).where(
            tables["contest_weeks"].c.id.in_(content["prospective_week_ids"])).values(team_membership_rules_version=PERSISTENT))
        for row in expected["contest_weeks"]:
            if row["id"] in content["prospective_week_ids"]:
                row["team_membership_rules_version"] = PERSISTENT
        connection.execute(update(tables["persistent_team_control"]).where(
            tables["persistent_team_control"].c.id == 1).values(activated_at=boundary,
                rules_from_week_start=date.fromisoformat(content["rules_from_week_start"])))
        expected["persistent_team_control"][0].update(activated_at=normalized(boundary),
            rules_from_week_start=content["rules_from_week_start"])
        after = _snapshot(connection, tables)
        if after != expected:
            raise CutoverError("unexpected_row_change: transaction_rolled_back")
        compatibility.assert_classroom_unchanged(connection, revision, classroom_before, CutoverError)
        receipt = {"transaction_state": "committed", "operation": "activated",
            "verification": {"passed": True, "before": _evidence(before), "after": _evidence(after),
                "historical_attribution_unchanged": True, "new_teams": 0, "new_memberships": 0,
                "activated_at": boundary.isoformat(), "approved_plan_sha256": expected_sha256}}
    return receipt


def verify_cutover(url, plan, expected_sha256, receipt):
    """Independently prove activation, then compare the immediate receipt evidence."""
    content = _approved(plan, expected_sha256)
    if (receipt.get("transaction_state") != "committed" or receipt.get("operation") != "activated"
            or receipt.get("verification", {}).get("approved_plan_sha256") != expected_sha256
            or receipt.get("verification", {}).get("passed") is not True):
        raise CutoverError("approved_activation_receipt_required")
    with inventory.readonly_connection(url) as connection:
        revision = _schema_guard(connection)
        _plan_schema_guard(content, revision)
        if _target(connection, url) != content["target"]:
            raise CutoverError("target_mismatch")
        rows = _snapshot(connection, _tables(connection))
        boundary = _boundary(content["rules_from_week_start"])
        control = rows["persistent_team_control"]
        if (len(control) != 1 or control[0]["id"] != 1
                or control[0]["activated_at"] is None
                or inventory.utc(control[0]["activated_at"]) != boundary
                or control[0]["rules_from_week_start"] != content["rules_from_week_start"]
                or control[0]["staged_plan_sha256"] != expected_sha256
                or control[0]["staged_plan"] != normalized(plan)
                or inventory.utc(control[0]["staged_for"]) != boundary):
            raise CutoverError("approved_activation_not_established")
        for name, ids, marker in (("teams", content["team_ids"], "is_operating"),
                                  ("team_memberships", content["membership_ids"], "is_persistent"),
                                  ("team_join_requests", content["join_request_ids"], "is_persistent")):
            if {row["id"] for row in rows[name] if row[marker]} != set(ids):
                raise CutoverError("approved_authority_markers_not_established")
        for week in rows["contest_weeks"]:
            expected_rules = PERSISTENT if week["id"] in content["prospective_week_ids"] else LEGACY
            if week["team_membership_rules_version"] != expected_rules:
                raise CutoverError("approved_week_rules_not_established")
        closing = next(w for w in rows["contest_weeks"] if w["id"] == content["current_legacy_week_id"])
        if closing["status"] != "finalized" and closing["finalized_at"] is None:
            if closing["team_roster_frozen_at"] is None or inventory.utc(closing["team_roster_frozen_at"]) != boundary:
                raise CutoverError("closing_legacy_roster_not_frozen")
            roster = _closing_roster(rows, closing, boundary)
            columns = ("contest_week_id", "profile_id", "team_id", "membership_id", "snapshot_at")
            actual = sorted([{key: row[key] for key in columns}
                for row in rows["team_week_membership_snapshots"] if row["contest_week_id"] == closing["id"]],
                key=lambda row: row["profile_id"])
            if actual != roster:
                raise CutoverError("closing_legacy_roster_not_established")
        evidence = _evidence(rows)
        if evidence != receipt["verification"].get("after"):
            raise CutoverError("post_activation_state_changed: do_not_reapply")
        return {"passed": True, "after": evidence, "persistent_authority_active": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "apply", "activate", "verify"))
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
            if args.operation in ("apply", "activate"):
                operation = apply_cutover if args.operation == "apply" else activate_cutover
                result = operation(url, plan, args.approved_sha256,
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
