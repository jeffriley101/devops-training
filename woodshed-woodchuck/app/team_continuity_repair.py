"""Explicit, reviewed team continuity maintenance. Never imported by runtime.

PLAN/VERIFY use H2A's read-only connections. APPLY calls H1B, owns one
transaction, and admits only Team/TeamMembership INSERTs. Operator flags attest
to external maintenance; they cannot pause a web service or finalization job.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from sqlalchemy import MetaData, Table, create_engine, event, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from . import team_continuity_inventory as inventory

REVISION = "s9n0o1p2q3r4"  # Descendants require explicit code/schema review.
CONFIRMATION = "APPLY TEAM CONTINUITY"
ACKS = ("backup_taken", "writers_paused", "finalization_paused", "maintenance_mode")
FIELDS = dict(inventory.FIELDS)
FIELDS["contest_weeks"] += " verification_deadline_at finalize_after"
FIELDS["camp_point_awards"] += " activity_type points_awarded"
FIELDS["practice_charts"] += " include_contests minutes source detected_playing_seconds"
FIELDS["contest_results"] += " score rank division subject_key"
FIELDS["team_families"] += " created_at"
HISTORY = ("team_families", "practice_charts", "camp_point_awards", "contest_results",
           "team_week_membership_snapshots", "reward_grants", "crown_awards",
           "team_join_requests", "team_reports", "director_team_contests",
           "director_team_contest_entries", "director_team_contest_results")


class RepairError(inventory.InventoryError):
    """A safe reason code/message; driver exceptions are never printed."""


def normalized(value):
    return json.loads(json.dumps(value, default=inventory.json_value, sort_keys=True))


def canonical(value):
    return json.dumps(normalized(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def clock(now=None):
    value = now if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise RepairError("aware_utc_time_required")
    return value.astimezone(timezone.utc)


def schema_guard(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if not {"alembic_version", "team_families", "teams"} <= tables:
        raise RepairError("h1a_schema_required: use H2A inventory before migration")
    versions = list(connection.scalars(text("SELECT version_num FROM alembic_version")))
    if versions != [REVISION]:
        raise RepairError("revision_not_approved: require s9n0o1p2q3r4; use H2A for older schemas")
    columns = {c["name"]: c for c in inspector.get_columns("teams")}
    fks = inspector.get_foreign_keys("teams")
    indexes = inspector.get_indexes("teams")
    uniques = inspector.get_unique_constraints("teams")
    valid = ("family_id" in columns and not columns["family_id"]["nullable"]
        and any(f["name"] == "fk_teams_family_id_team_families"
                and f["constrained_columns"] == ["family_id"]
                and f["referred_table"] == "team_families" and f["referred_columns"] == ["id"]
                and f.get("options", {}).get("ondelete", "").upper() == "RESTRICT" for f in fks)
        and any(i["name"] == "ix_teams_family_id" and i["column_names"] == ["family_id"] for i in indexes)
        and any(u["name"] == "uq_team_season_family" and u["column_names"] == ["season_id", "family_id"] for u in uniques))
    if not valid:
        raise RepairError("team_family_constraints_missing")
    for name in set(HISTORY) | {"seasons", "team_memberships", "contest_weeks",
                               "woodchuck_profiles", "profile_capabilities"}:
        if name not in tables or set(FIELDS[name].split()) - {c["name"] for c in inspector.get_columns(name)}:
            raise RepairError("required_schema_incomplete: " + name)


class Snapshot:
    def __init__(self, connection):
        self.connection = connection
        self.metadata = MetaData()

    def rows(self, name, condition=None):
        table = Table(name, self.metadata, autoload_with=self.connection, resolve_fks=False)
        columns = [table.c[f] for f in FIELDS[name].split()]
        if name == "teams":
            # Private code never leaves this module in cleartext.
            columns.append(table.c.join_code)
        statement = select(*columns).order_by(table.c.id)
        if condition is not None:
            statement = statement.where(condition(table))
        rows = [dict(r) for r in self.connection.execute(statement).mappings()]
        for row in rows:
            if "join_code" in row:
                code = row.pop("join_code")
                row["join_code_sha256"] = digest(code) if code else None
        return normalized(rows)


def target_identity(connection, url):
    identity = inventory.target_metadata(inventory.target_url(url))
    if connection.dialect.name == "postgresql":
        identity["schema"] = connection.scalar(text("SELECT current_schema()"))
        identity["database"] = connection.scalar(text("SELECT current_database()"))
    else:
        identity["database"] = str(Path(identity["database"]).expanduser().resolve(strict=True))
    return identity


def season_pair(session, source_key, destination_key):
    from .models import Season
    seasons = list(session.scalars(select(Season).order_by(Season.id)
                                  .execution_options(populate_existing=True)))
    source = next((s for s in seasons if s.key == source_key), None)
    dest = next((s for s in seasons if s.key == destination_key), None)
    if source is None or dest is None or source.id == dest.id:
        raise RepairError("explicit_existing_distinct_seasons_required")
    if (source.ends_on is None or source.ends_on + timedelta(days=1) != dest.starts_on
            or dest.starts_on <= source.starts_on or source.timezone != dest.timezone):
        raise RepairError("nonadjacent_or_incoherent_seasons")
    if any(s.id != dest.id and s.starts_on <= dest.starts_on
           and (s.ends_on is None or s.ends_on >= dest.starts_on) for s in seasons):
        raise RepairError("ambiguous_boundary_season")
    return source, dest


def build_plan(session, url, source_key, destination_key, *, now=None, preflight=False):
    from . import team_continuity as domain
    moment = clock(now)
    connection = session.connection()
    schema_guard(connection)
    source, dest = season_pair(session, source_key, destination_key)
    planner = domain.preflight_team_continuity if preflight else domain.plan_team_continuity
    plan = planner(session, source_season_id=source.id,
                                      destination_season_id=dest.id, now=moment)
    snapshot = Snapshot(connection)
    teams = snapshot.rows("teams", lambda t: t.c.season_id.in_([source.id, dest.id]))
    team_ids = [t["id"] for t in teams]
    members = snapshot.rows("team_memberships", lambda t:
        t.c.season_id.in_([source.id, dest.id]) | t.c.team_id.in_(team_ids))
    profile_ids = {m["profile_id"] for m in members} | {t["creator_profile_id"] for t in teams
                                                           if t["creator_profile_id"] is not None}
    weeks = snapshot.rows("contest_weeks", lambda t: t.c.season_id.in_([source.id, dest.id]))
    history = {name: snapshot.rows(name) for name in HISTORY}
    source_final = [w for w in weeks if w["season_id"] == source.id
                    and w["week_start"] <= source.ends_on.isoformat() < w["week_end"]]
    dest_first = [w for w in weeks if w["season_id"] == dest.id
                  and w["week_start"] <= dest.starts_on.isoformat() < w["week_end"]]
    guards = list(plan.reasons)
    if len(source_final) != 1:
        guards.append("source_final_week_missing_or_ambiguous")
    elif source_final[0]["status"] != "finalized":
        if moment >= inventory.utc(source_final[0]["finalize_after"]):
            guards.append("source_due_unfinalized: finalize normally then replan")
        elif source_final[0]["status"] != "open" or source_final[0]["finalized_at"] is not None:
            guards.append("source_final_week_inconsistent")
    elif source_final[0]["finalized_at"] is None:
        guards.append("source_final_week_inconsistent")
    if len(dest_first) != 1 or dest_first[0]["status"] != "open" or dest_first[0]["finalized_at"] is not None:
        guards.append("destination_first_week_not_open")
    dest_week_ids = {w["id"] for w in weeks if w["season_id"] == dest.id}
    frozen_counts = {name: sum(r["contest_week_id"] in dest_week_ids for r in history[name])
                     for name in ("contest_results", "team_week_membership_snapshots")}
    dest_results = {r["id"] for r in history["contest_results"] if r["contest_week_id"] in dest_week_ids}
    frozen_counts["reward_grants"] = sum(r["contest_result_id"] in dest_results for r in history["reward_grants"])
    dest_contests = {r["id"] for r in history["director_team_contests"] if r["season_id"] == dest.id}
    frozen_counts["director_team_contest_results"] = sum(r["contest_id"] in dest_contests for r in history["director_team_contest_results"])
    if any(frozen_counts.values()):
        guards.append("destination_frozen_artifacts")
    if any(inventory.utc(c["starts_at"]) <= plan.boundary <= inventory.utc(c["ends_at"])
           for c in history["director_team_contests"] if plan.boundary is not None):
        guards.append("boundary_director_contest_review")
    actions = []
    for action in plan.teams:
        item = asdict(action)
        original = next(t for t in teams if t["id"] == action.source_team_id)
        item["source_display_name"] = original["display_name"]
        item["action"] = "reuse_successor" if action.successor_team_id else "create_successor"
        for member in item["members"]:
            member.update({"source_team_id": action.source_team_id, "destination_family_id": action.family_id,
                           "started_at": plan.boundary, "selected_week_start": plan.selected_week_start})
        actions.append(item)
    classifications = [t["classification"] for t in actions] + [m["classification"] for t in actions for m in t["members"]]
    summary = {"source_teams_considered": len(actions),
        "teams_to_create": sum(t["classification"] == "SAFE" and not t["successor_team_id"] for t in actions),
        "teams_represented": sum(t["classification"] == "SAFE" and bool(t["successor_team_id"]) for t in actions),
        "memberships_to_create": sum(t["classification"] == m["classification"] == "SAFE" and not m["destination_membership_id"] for t in actions for m in t["members"]),
        "memberships_represented": sum(bool(m["destination_membership_id"]) for t in actions for m in t["members"]),
        "review_count": classifications.count("REVIEW"), "conflict_count": classifications.count("CONFLICT"),
        "collision_count": sum("collision" in reason for t in actions for reason in t["reasons"]),
        "frozen_count": sum(frozen_counts.values())}
    content = normalized({"version": 1, "revision": REVISION,
        "target": target_identity(connection, url),
        "source": {"id": source.id, "key": source.key}, "destination": {"id": dest.id, "key": dest.key},
        "boundary": plan.boundary, "selected_week_start": plan.selected_week_start,
        "state": {"seasons": snapshot.rows("seasons"), "teams": teams, "memberships": members, "weeks": weeks,
            "profiles": snapshot.rows("woodchuck_profiles", lambda t: t.c.id.in_(profile_ids)),
            "capabilities": snapshot.rows("profile_capabilities", lambda t: t.c.profile_id.in_(profile_ids)),
            "history": {name: {"count": len(rows), "sha256": digest(rows)} for name, rows in history.items()}},
        "preconditions": {"source_final_week": source_final, "destination_first_week": dest_first,
            "destination_team_count": sum(t["season_id"] == dest.id for t in teams),
            "destination_membership_history_count": sum(m["season_id"] == dest.id for m in members),
            "frozen_counts": frozen_counts, "blockers": sorted(set(guards))},
        "actions": actions, "summary": summary})
    return {"operation": "plan", "metadata": {"generated_at": moment.isoformat(),
        "application_commit": inventory.local_commit()}, "content": content, "plan_sha256": digest(content)}


def generate_plan(url, source, destination, *, now=None):
    with inventory.readonly_connection(url) as connection:
        with Session(connection, autoflush=False) as session:
            return build_plan(session, url, source, destination, now=now)


def approved_content(approved, expected):
    try:
        content = approved["content"]
        if content["version"] != 1 or content["revision"] != REVISION:
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError
        if not hmac.compare_digest(digest(content), expected) or not hmac.compare_digest(approved["plan_sha256"], expected):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RepairError("approved_plan_fingerprint_invalid") from None
    return content


def require_safe(content, *, database_generated=False):
    if content["preconditions"]["blockers"]:
        # Do not echo arbitrary text from an operator-supplied plan file.
        if database_generated:
            raise RepairError("maintenance_preconditions_blocked: " + ", ".join(content["preconditions"]["blockers"]))
        raise RepairError("maintenance_preconditions_blocked: review a freshly generated plan")
    if content["summary"]["review_count"] or content["summary"]["conflict_count"]:
        raise RepairError("review_or_conflict_requires_new_plan: no partial repair permitted")


def verify_content(before, current):
    """Accept only the exact H1B successor/roster delta; everything else equal.

    Used after insertion (before commit) and by read-only VERIFY. Never trusts
    IDs from an apply report. Repeating APPLY requires a current reviewed plan.
    """
    require_safe(before)
    require_safe(current, database_generated=True)
    state = deepcopy(current["state"])
    old_state = before["state"]
    created_teams, created_members = [], []
    for action in before["actions"]:
        matches = [t for t in state["teams"] if t["family_id"] == action["family_id"]
                   and t["season_id"] == before["destination"]["id"]]
        if len(matches) != 1:
            raise RepairError("verification_successor_missing_or_ambiguous")
        successor = matches[0]
        source = next(t for t in old_state["teams"] if t["id"] == action["source_team_id"])
        from .team_continuity import IDENTITY_FIELDS
        if any(successor[f] != source[f] for f in IDENTITY_FIELDS) or successor["moderation_status"] != "active":
            raise RepairError("verification_successor_identity_changed")
        if not action["successor_team_id"]:
            if successor["id"] in {t["id"] for t in old_state["teams"]}:
                raise RepairError("verification_unexpected_existing_team")
            if source["director_led"] and (not successor["join_code_sha256"] or successor["join_code_sha256"] == source["join_code_sha256"]):
                raise RepairError("verification_private_code_not_rotated")
            created_teams.append(successor["id"])
        elif successor["id"] != action["successor_team_id"]:
            raise RepairError("verification_existing_successor_changed")
        for member in action["members"]:
            if member["destination_membership_id"]:
                continue
            matches = [m for m in state["memberships"] if m["season_id"] == before["destination"]["id"]
                       and m["profile_id"] == member["profile_id"]]
            if (len(matches) != 1 or matches[0]["team_id"] != successor["id"]
                    or matches[0]["started_at"] != before["boundary"] or matches[0]["ended_at"] is not None
                    or matches[0]["selected_week_start"] != before["selected_week_start"]):
                raise RepairError("verification_membership_delta_mismatch")
            created_members.append(matches[0]["id"])
    state["teams"] = [t for t in state["teams"] if t["id"] not in created_teams]
    state["memberships"] = [m for m in state["memberships"] if m["id"] not in created_members]
    if state != old_state:
        raise RepairError("verification_history_or_preconditions_changed")
    for key in ("target", "source", "destination", "boundary", "selected_week_start", "revision"):
        if before[key] != current[key]:
            raise RepairError("verification_target_or_transition_changed")
    if current["summary"]["teams_to_create"] or current["summary"]["memberships_to_create"]:
        raise RepairError("verification_additional_creates_remain")
    return {"passed": True, "successor_team_ids": created_teams, "destination_membership_ids": created_members,
            "new_team_count": len(created_teams), "new_membership_count": len(created_members),
            "protected_history_unchanged": True, "attribution_changes": 0, "remaining_creates": 0}


@contextmanager
def writer(url):
    target = inventory.target_url(url)
    if target.get_backend_name() == "sqlite":
        path = Path(target.database).expanduser().resolve(strict=True)
        def connect():
            raw = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=15)
            raw.execute("PRAGMA foreign_keys=ON")
            return raw
        engine = create_engine("sqlite://", creator=connect, poolclass=NullPool)
    else:
        engine = create_engine(target, isolation_level="READ COMMITTED", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


@contextmanager
def insertion_fence(connection, session):
    """Reject UPDATE/DELETE/DDL and every INSERT outside H1B's two tables."""
    def sql_guard(conn, cursor, statement, parameters, context, many):
        sql = statement.strip()
        if re.match(r"^(SELECT|PRAGMA|SHOW|BEGIN|LOCK TABLE)\b", sql, re.I):
            return
        if re.match(r'^INSERT INTO (?:"?teams"?|"?team_memberships"?)\s*\(', sql, re.I):
            return
        raise RepairError("unexpected_sql_mutation_blocked")
    def flush_guard(s, context, instances):
        from .models import Team, TeamMembership
        if s.dirty or s.deleted or any(type(row) not in (Team, TeamMembership) for row in s.new):
            raise RepairError("unexpected_orm_mutation_blocked")
    event.listen(connection, "before_cursor_execute", sql_guard)
    event.listen(session, "before_flush", flush_guard)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", sql_guard)
        event.remove(session, "before_flush", flush_guard)


def apply_repair(url, approved, expected, *, acknowledgments, confirmation, now=None, prepare=None):
    from . import team_continuity as domain
    if set(ACKS) - {key for key, value in acknowledgments.items() if value is True} or confirmation != CONFIRMATION:
        raise RepairError("all_maintenance_acknowledgments_and_exact_confirmation_required")
    before = approved_content(approved, expected)
    require_safe(before)
    with writer(url) as connection:
        schema_guard(connection)
        with Session(connection, autoflush=False, expire_on_commit=False) as session:
            try:
                source, dest = season_pair(session, before["source"]["key"], before["destination"]["key"])
                domain.lock_continuity_rows(session, source_season_id=source.id, destination_season_id=dest.id)
                with insertion_fence(connection, session):
                    current = build_plan(session, url, source.key, dest.key, now=now)
                    require_safe(current["content"], database_generated=True)
                    if not hmac.compare_digest(current["plan_sha256"], expected):
                        raise RepairError("stale_plan: database differs; no repair performed; generate and review a new plan")
                    domain.apply_team_continuity(session, source_season_id=source.id,
                                                 destination_season_id=dest.id, now=clock(now))
                    session.flush()
                    after = build_plan(session, url, source.key, dest.key, now=now)
                    verification = verify_content(before, after["content"])
                    outcome = "applied" if verification["new_team_count"] or verification["new_membership_count"] else "no_op"
                    report = {"operation": "apply", "outcome": outcome, "plan_sha256": expected,
                              "metadata": after["metadata"], "revision": REVISION,
                              "target": after["content"]["target"], "verification": verification,
                              "post_plan_sha256": after["plan_sha256"], "transaction_state": "prepared_not_committed"}
                    if prepare:
                        prepare(report)  # Durable evidence prepared BEFORE commit.
                    # Recheck the wall-clock guard after evidence I/O too.
                    last = build_plan(session, url, source.key, dest.key, now=now)
                    verify_content(before, last["content"])
                session.flush()
                connection.commit()
                report["transaction_state"] = "committed"
                return report
            except BaseException:
                connection.rollback()
                raise


def verify_repair(url, approved, expected, *, now=None):
    before = approved_content(approved, expected)
    current = generate_plan(url, before["source"]["key"], before["destination"]["key"], now=now)
    return {"operation": "verify", "metadata": current["metadata"], "revision": REVISION,
            "plan_sha256": expected, "current_plan_sha256": current["plan_sha256"],
            "target": current["content"]["target"], "verification": verify_content(before, current["content"])}


@contextmanager
def exclusive_report(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        def save(value):
            stream.seek(0)
            json.dump(normalized(value), stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        yield save


def load_plan(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        raise RepairError("approved_plan_unreadable_or_invalid") from None


def main(argv=None):
    parser = inventory.SafeArgumentParser(description="Explicit team continuity maintenance; no automatic repair.")
    commands = parser.add_subparsers(dest="operation", required=True, parser_class=inventory.SafeArgumentParser)
    for mode in ("plan", "apply", "verify"):
        command = commands.add_parser(mode)
        command.add_argument("--database-url", help="Explicit target, or deliberately set DATABASE_URL; credentials never printed.")
        command.add_argument("--output", required=True, help="New operator evidence file; exclusive 0600 creation.")
        if mode == "plan":
            command.add_argument("--source-season", required=True)
            command.add_argument("--destination-season", required=True)
        else:
            command.add_argument("--plan", required=True)
            command.add_argument("--plan-sha256", required=True)
        if mode == "apply":
            command.add_argument("--apply", action="store_true", required=True)
            for ack in ACKS:
                command.add_argument("--ack-" + ack.replace("_", "-"), action="store_true", required=True, dest=ack)
            command.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    committed = False
    try:
        url = args.database_url or os.environ.get("DATABASE_URL")
        inventory.target_url(url)
        approved = load_plan(args.plan) if args.operation != "plan" else None
        # Reserve the output BEFORE any operation; existing evidence is untouched.
        with exclusive_report(args.output) as save:
            save({"operation": args.operation, "transaction_state": "requested_not_completed",
                  "at": clock().isoformat(), "application_commit": inventory.local_commit()})
            if args.operation == "plan":
                report = generate_plan(url, args.source_season, args.destination_season)
            elif args.operation == "apply":
                report = apply_repair(url, approved, args.plan_sha256,
                    acknowledgments={ack: getattr(args, ack) for ack in ACKS}, confirmation=args.confirm, prepare=save)
                committed = True
            else:
                report = verify_repair(url, approved, args.plan_sha256)
            save(report)
        print("PLAN SHA-256: " + report["plan_sha256"])
        print(json.dumps(report["content"]["summary"] if args.operation == "plan" else report["verification"], sort_keys=True))
        if args.operation == "plan":
            print("BLOCKERS: " + json.dumps(report["content"]["preconditions"]["blockers"]))
            print("READ-ONLY PLAN — NO REPAIR PERFORMED")
        else:
            print(report.get("outcome", "READ-ONLY VERIFICATION"))
        return 0
    except inventory.InventoryError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print("Maintenance failed; connection/driver details withheld. Check target, schema and evidence path.", file=sys.stderr)
    if committed:
        print("DATABASE COMMITTED; final evidence write failed. Run VERIFY with the approved plan.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
