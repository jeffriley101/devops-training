"""Explicit operational jobs for prospective readiness and boundary activation.

No web/startup caller. Preflight never grants permission to write; activation
plans from current locked state and admits only the existing H1B insert delta.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import json
import os
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import team_continuity as domain, team_continuity_repair as repair
from .models import Season


def boundary(season):
    return datetime.combine(season.starts_on, time.min, ZoneInfo(season.timezone)).astimezone(timezone.utc)


def discover(session, *, mode, now, source=None, destination=None):
    """Durable date-covered records, never hard-coded IDs or status alone."""
    if bool(source) != bool(destination):
        raise repair.RepairError("both_season_keys_required")
    if source:
        pair = repair.season_pair(session, source, destination)
    else:
        seasons = list(session.scalars(select(Season).order_by(Season.starts_on, Season.id)
                                      .execution_options(populate_existing=True)))
        enabled = [s for s in seasons if s.status == "active"]
        current = [s for s in enabled if s.starts_on <= now.astimezone(ZoneInfo(s.timezone)).date()
                   and (s.ends_on is None or now.astimezone(ZoneInfo(s.timezone)).date() <= s.ends_on)]
        if len(current) > 1:
            raise repair.RepairError("ambiguous_current_season")
        if mode == "team_preflight":
            upcoming = [s for s in enabled if boundary(s) > now]
            if not upcoming:
                return None
            first = min(boundary(s) for s in upcoming)
            candidates = [s for s in upcoming if boundary(s) == first]
        else:
            candidates = current
        if not candidates:
            return None
        if len(candidates) != 1:
            raise repair.RepairError("ambiguous_destination_season")
        dest = candidates[0]
        predecessors = [s for s in seasons if s.ends_on is not None
                        and s.ends_on + timedelta(days=1) == dest.starts_on]
        if not predecessors:
            if any(s.starts_on < dest.starts_on for s in seasons):
                raise repair.RepairError("adjacent_source_missing")
            return None
        if len(predecessors) != 1:
            raise repair.RepairError("ambiguous_source_season")
        pair = repair.season_pair(session, predecessors[0].key, dest.key)
    src, dest = pair
    if src.status not in {"active", "closed"} or dest.status != "active":
        raise repair.RepairError("season_not_enabled")
    # Check source coverage as well as the destination boundary.
    seasons = list(session.scalars(select(Season)))
    if any(s.id not in {src.id, dest.id} and s.starts_on <= src.ends_on
           and (s.ends_on is None or s.ends_on >= src.starts_on) for s in seasons):
        raise repair.RepairError("ambiguous_source_coverage")
    if mode == "team_activate" and dest.ends_on is not None and now.astimezone(ZoneInfo(dest.timezone)).date() > dest.ends_on:
        raise repair.RepairError("destination_expired_use_reviewed_maintenance")
    return pair


def result(content, *, mode):
    summary = content["summary"]
    blockers = content["preconditions"]["blockers"]
    reasons = set(blockers)
    for team in content["actions"]:
        if team["classification"] != "SAFE":
            reasons.update(team["reasons"])
        for member in team["members"]:
            if member["classification"] != "SAFE":
                reasons.add(member["reason"])
    status = "NOT_READY" if reasons else "READY"
    if reasons == {"boundary_not_reached"}:
        status = "NOT_DUE"
    if not reasons and not summary["teams_to_create"] and not summary["memberships_to_create"]:
        status = "ALREADY_COMPLETE"
    return {"operation": mode, "status": status, "source": content["source"],
            "destination": content["destination"], "boundary": content["boundary"],
            "source_team_count": sum(t["season_id"] == content["source"]["id"] for t in content["state"]["teams"]),
            "expected_boundary_membership_count": sum(len(t["members"]) for t in content["actions"]),
            **summary,
            "destination_team_count": content["preconditions"]["destination_team_count"],
            "destination_membership_history_count": content["preconditions"]["destination_membership_history_count"],
            "destination_frozen": bool(summary["frozen_count"] or any(
                w["status"] == "finalized" or w["finalized_at"] is not None for w in content["state"]["weeks"]
                if w["season_id"] == content["destination"]["id"])),
            "reason_codes": sorted(reasons)}


def preflight(url, *, source=None, destination=None, now=None):
    moment = repair.clock(now)
    with repair.inventory.readonly_connection(url) as connection:
        repair.schema_guard(connection)
        with Session(connection, autoflush=False) as session:
            pair = discover(session, mode="team_preflight", now=moment, source=source, destination=destination)
            if pair is None:
                return {"operation": "team_preflight", "status": "NO_TRANSITION", "reason_codes": []}
            plan = repair.build_plan(session, url, pair[0].key, pair[1].key, now=moment, preflight=True)
            return result(plan["content"], mode="team_preflight")


def activate(url, *, source=None, destination=None, now=None):
    with repair.writer(url) as connection:
        try:
            repair.schema_guard(connection)
            with Session(connection, autoflush=False, expire_on_commit=False) as session:
                pair = discover(session, mode="team_activate", now=repair.clock(now), source=source, destination=destination)
                if pair is None:
                    return {"operation": "team_activate", "status": "NO_TRANSITION", "reason_codes": []}
                src, dest = pair
                domain.lock_continuity_rows(session, source_season_id=src.id, destination_season_id=dest.id)
                # Discovery and all dates/statuses are revalidated AFTER locks.
                src, dest = discover(session, mode="team_activate", now=repair.clock(now), source=src.key, destination=dest.key)
                with repair.insertion_fence(connection, session):
                    before = repair.build_plan(session, url, src.key, dest.key, now=now)["content"]
                    report = result(before, mode="team_activate")
                    if report["status"] in {"NOT_READY", "NOT_DUE"}:
                        connection.rollback()
                        return report
                    repair.require_safe(before, database_generated=True)
                    domain.apply_team_continuity(session, source_season_id=src.id,
                                                 destination_season_id=dest.id, now=repair.clock(now))
                    session.flush()
                    after = repair.build_plan(session, url, src.key, dest.key, now=now)["content"]
                    verification = repair.verify_content(before, after)
                    report.update({"status": "ALREADY_COMPLETE" if not verification["new_team_count"] and not verification["new_membership_count"] else "READY",
                                   "teams_created": verification["new_team_count"],
                                   "memberships_created": verification["new_membership_count"],
                                   "verification": verification,
                                   "remaining_team_creates": after["summary"]["teams_to_create"],
                                   "remaining_membership_creates": after["summary"]["memberships_to_create"]})
                    # Use a fresh wall clock immediately before commit, including
                    # when locks/verification straddle the stored source deadline.
                    repair.verify_content(before, repair.build_plan(session, url, src.key, dest.key, now=now)["content"])
                connection.commit()
                return report
        except BaseException:
            connection.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit seasonal team jobs; preflight is read-only.")
    parser.add_argument("operation", choices=("team_preflight", "team_activate"))
    parser.add_argument("--database-url", help="Explicit target; alternatively set DATABASE_URL")
    parser.add_argument("--source-season")
    parser.add_argument("--destination-season")
    args = parser.parse_args(argv)
    try:
        url = args.database_url or os.environ.get("DATABASE_URL")
        repair.inventory.target_url(url)  # No fallback to the app's local DB.
        operation = preflight if args.operation == "team_preflight" else activate
        report = operation(url, source=args.source_season, destination=args.destination_season)
        print(json.dumps(report, sort_keys=True))
        return 1 if report["status"] == "NOT_READY" else 0
    except repair.inventory.InventoryError as error:
        print(json.dumps({"operation": args.operation, "status": "NOT_READY", "reason_codes": [str(error)]}), file=sys.stderr)
    except Exception:
        print(json.dumps({"operation": args.operation, "status": "NOT_READY", "reason_codes": ["activation_failed_driver_details_withheld"]}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
