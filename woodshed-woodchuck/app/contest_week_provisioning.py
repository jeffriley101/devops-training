"""Explicit insert-only calendar preparation; no runtime or scheduler hook."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
import os
import sys

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from . import team_continuity as domain, team_continuity_repair as repair
from .contests import CENTRAL, aware_utc, contest_week_schedule
from .models import ContestWeek, Season
from .seasons import CANONICAL_SEASONS, SeasonConfigurationError, validate_definition


def scope(*, seasons=(), source=None, destination=None):
    """No discovery, arbitrary dates, or inferred end for an open-ended season."""
    definitions = {s.key: s for s in CANONICAL_SEASONS}
    if seasons:
        if source or destination:
            raise repair.RepairError("choose_seasons_or_transition")
        keys = list(set(seasons))
    else:
        if not source or not destination or source == destination:
            raise repair.RepairError("explicit_seasons_or_adjacent_transition_required")
        keys = [source, destination]
    if any(k not in definitions for k in keys):
        raise repair.RepairError("canonical_season_keys_required")
    if seasons:
        keys.sort(key=lambda k: definitions[k].starts_on)
    selected = [definitions[k] for k in keys]
    if not seasons:
        src, dest = selected
        if src.ends_on is None or src.ends_on + timedelta(days=1) != dest.starts_on:
            raise repair.RepairError("adjacent_canonical_seasons_required")
        starts = [(src, src.ends_on - timedelta(days=6)), (dest, dest.starts_on)]
    else:
        starts = []
        for definition in selected:
            if definition.ends_on is None:
                raise repair.RepairError("canonical_season_end_unknown_use_bounded_transition")
            start = definition.starts_on
            while start <= definition.ends_on:
                starts.append((definition, start))
                start += timedelta(days=7)
    for definition, start in starts:
        end, _, _ = contest_week_schedule(start)
        if start < definition.starts_on or (definition.ends_on is not None and end > definition.ends_on + timedelta(days=1)):
            raise repair.RepairError("canonical_complete_weeks_required")
    return selected, starts


def build_plan(session, *, seasons=(), source=None, destination=None):
    selected, starts = scope(seasons=seasons, source=source, destination=destination)
    rows = list(session.scalars(select(Season).order_by(Season.id).execution_options(populate_existing=True)))
    by_key = {row.key: row for row in rows}
    conflicts = []
    prerequisites = ["run_team_preflight_before_activation", "activate_at_real_boundary", "finalize_source_separately_after_stored_deadlines"]
    for definition in selected:
        row = by_key.get(definition.key)
        if row is None:
            conflicts.append({"reason": "season_missing", "season": definition.key})
            continue
        try:
            validate_definition(row, definition)
        except SeasonConfigurationError:
            conflicts.append({"reason": "canonical_season_conflict", "season": definition.key})
        for other in rows:
            if other.id != row.id and (definition.ends_on is None or other.starts_on <= definition.ends_on) and (other.ends_on is None or other.ends_on >= definition.starts_on):
                conflicts.append({"reason": "overlapping_season", "season": definition.key, "other_season_id": other.id})
        if row.status != "active" and not (row.key == source and row.status == "closed"):
            prerequisites.append(f"season_not_active:{row.key}:{row.status}")
        if seasons:
            outside = list(session.scalars(select(ContestWeek.id).where(
                ContestWeek.season_id == row.id,
                or_(ContestWeek.week_start < definition.starts_on,
                    ContestWeek.week_start > definition.ends_on,
                    ContestWeek.week_end <= definition.starts_on,
                    ContestWeek.week_end > definition.ends_on + timedelta(days=1)),
            ).order_by(ContestWeek.id)))
            if outside:
                conflicts.append({"reason": "weeks_outside_canonical_season", "season": row.key, "week_ids": outside})

    weeks = []
    for definition, start in starts:
        end, deadline, finalize = contest_week_schedule(start)
        season = by_key.get(definition.key)
        # Include malformed rows starting inside the interval too.
        # Half-open intervals allow adjacent weeks to remain independent.
        overlaps = list(session.scalars(select(ContestWeek).where(or_(
            (ContestWeek.week_start >= start) & (ContestWeek.week_start < end),
            (ContestWeek.week_start < end) & (ContestWeek.week_end > start),
        )).order_by(ContestWeek.id).execution_options(populate_existing=True)))
        matching = [w for w in overlaps if season is not None and w.season_id == season.id and w.week_start == start and w.week_end == end]
        item = {"season": definition.key, "season_id": season.id if season else None,
                "week_start": start.isoformat(), "week_end": end.isoformat(),
                "verification_deadline_at": deadline.isoformat(), "finalize_after": finalize.isoformat(),
                "action": "missing"}
        if len(overlaps) == 1 and len(matching) == 1:
            week = matching[0]
            item.update(action="unchanged", id=week.id, status=week.status,
                        finalized_at=aware_utc(week.finalized_at).isoformat() if week.finalized_at else None,
                        verification_deadline_at=aware_utc(week.verification_deadline_at).isoformat(),
                        finalize_after=aware_utc(week.finalize_after).isoformat(),
                        stored_deadlines_differ=(aware_utc(week.verification_deadline_at) != deadline or aware_utc(week.finalize_after) != finalize))
            if definition.key == destination and (week.status != "open" or week.finalized_at is not None):
                prerequisites.append("destination_first_week_not_open")
        elif overlaps:
            item["action"] = "conflict"
            conflicts.append({"reason": "conflicting_or_overlapping_weeks", "season": definition.key,
                              "week_start": start.isoformat(), "weeks": [
                                  {"id": w.id, "season_id": w.season_id,
                                   "week_start": w.week_start.isoformat(), "week_end": w.week_end.isoformat()}
                                  for w in overlaps]})
        weeks.append(item)
    return {"operation": "provision_weeks", "mode": "plan", "status": "BLOCKED" if conflicts else "READY",
            "scope": {"seasons": [s.key for s in selected], "kind": "seasons" if seasons else "transition"},
            "boundary": datetime.combine(selected[1].starts_on, time.min, CENTRAL).astimezone(timezone.utc).isoformat() if destination else None,
            "weeks": weeks, "missing": sum(w["action"] == "missing" for w in weeks),
            "unchanged": sum(w["action"] == "unchanged" for w in weeks), "created": 0,
            "conflicts": conflicts, "activation_prerequisites": sorted(set(prerequisites))}


def provision(url, *, apply=False, seasons=(), source=None, destination=None):
    """Plan on an enforced read-only snapshot, or replan under writer locks.

    Own the whole transaction. Matching rows are never assigned/flushed, even
    when their deadlines or frozen state differ from today's scheduling rules.
    """
    selected, _ = scope(seasons=seasons, source=source, destination=destination)
    arguments = dict(seasons=seasons, source=source, destination=destination)
    connect = repair.writer if apply else repair.inventory.readonly_connection
    with connect(url) as connection:
        try:
            repair.schema_guard(connection)
            with Session(connection, autoflush=False, expire_on_commit=False) as session:
                if apply:
                    ids = list(session.scalars(select(Season.id).where(Season.key.in_([s.key for s in selected]))))
                    domain.lock_team_seasons(session, *ids)
                    if connection.dialect.name == "postgresql":
                        # Also fence legacy/lazy calendar writers which don't take
                        # season locks. The per-season unique constraint alone
                        # cannot reject cross-season interval overlaps.
                        connection.execute(text("LOCK TABLE contest_weeks IN SHARE ROW EXCLUSIVE MODE"))
                report = build_plan(session, **arguments)
                if not apply:
                    return report
                report["mode"] = "apply"
                if report["conflicts"]:
                    connection.rollback()
                    return report
                for week in report["weeks"]:
                    if week["action"] == "missing":
                        session.add(ContestWeek(
                            season_id=week["season_id"],
                            week_start=date.fromisoformat(week["week_start"]),
                            week_end=date.fromisoformat(week["week_end"]),
                            verification_deadline_at=datetime.fromisoformat(week["verification_deadline_at"]),
                            finalize_after=datetime.fromisoformat(week["finalize_after"]), status="open"))
                session.flush()
                after = build_plan(session, **arguments)
                if after["conflicts"] or after["missing"]:
                    raise repair.RepairError("provisioning_verification_failed")
                inserted = {(w["season_id"], w["week_start"]) for w in report["weeks"] if w["action"] == "missing"}
                for week in after["weeks"]:
                    if (week["season_id"], week["week_start"]) in inserted:
                        week["action"] = "created"
                after.update(mode="apply", status="APPLIED" if inserted else "ALREADY_COMPLETE",
                             created=len(inserted), unchanged=report["unchanged"])
                connection.commit()
                return after
        except BaseException:
            connection.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only canonical week plan; --apply inserts missing weeks atomically.")
    parser.add_argument("--database-url")
    parser.add_argument("--season", action="append", default=[])
    parser.add_argument("--source-season")
    parser.add_argument("--destination-season")
    parser.add_argument("--apply", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        report = provision(args.database_url or os.environ.get("DATABASE_URL"), apply=args.apply,
                           seasons=args.season, source=args.source_season, destination=args.destination_season)
        print(json.dumps(report, sort_keys=True))
        return 1 if report["status"] == "BLOCKED" else 0
    except repair.inventory.InventoryError as error:
        reason = str(error)
    except Exception:
        reason = "provisioning_failed_transaction_rolled_back_driver_details_withheld"
    print(json.dumps({"operation": "provision_weeks", "status": "BLOCKED", "reason": reason}), file=sys.stderr)
    return 1
