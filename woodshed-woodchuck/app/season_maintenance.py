"""Explicit canonical-calendar bootstrap/repair. CLI defaults to a read-only plan.

No startup hook invokes repair. Only Season.ends_on (the legacy open-ended Band
Camp exception) and ContestWeek.season_id can be changed on existing rows.
"""
import argparse
import json
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, inspect, or_, select, text

from .contests import CENTRAL, CONTEST_DEFINITIONS, aware_utc, contest_week_schedule
from .db import SessionLocal
from .models import (CampPointAward, Contest, ContestResult, ContestWeek, CrownAward,
                     CrownProgress, DirectorTeamContest, PracticeChart, RewardGrant,
                     RewardInventoryPlacement, Season, Team, TeamJoinRequest,
                     TeamMembership, TeamWeekMembershipSnapshot)
from .seasons import (CANONICAL_SEASONS, SeasonConfigurationError,
                      bootstrap_canonical_seasons, canonical_definition_for_date)


def _count(session, model, *conditions):
    return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def _dependencies(session, week, target):
    results = session.scalars(select(ContestResult).where(ContestResult.contest_week_id == week.id)).all()
    snapshots = session.scalars(select(TeamWeekMembershipSnapshot).where(
        TeamWeekMembershipSnapshot.contest_week_id == week.id)).all()
    result_ids = [row.id for row in results]
    grants = session.scalars(select(RewardGrant).where(or_(
        RewardGrant.contest_result_id.in_(result_ids), RewardGrant.source_key.like(f"contest:{week.id}:%"),
    ))).all()
    crowns = session.scalars(select(CrownAward).where(or_(
        CrownAward.source_key.like(f"contest:{week.id}:%"),
        CrownAward.source_key.in_([grant.source_key for grant in grants]),
    ))).all()
    profiles = {r.profile_id for r in results if r.profile_id is not None} | {g.profile_id for g in grants}
    start = datetime.combine(week.week_start, time.min, CENTRAL).astimezone(timezone.utc)
    end = datetime.combine(week.week_end, time.min, CENTRAL).astimezone(timezone.utc)
    counts = {
        "contest_results": len(results), "membership_snapshots": len(snapshots),
        "reward_grants": len(grants), "crown_awards": len(crowns),
        # Progress is lifetime/profile/category state, not a week FK. Report
        # all progress for affected recipients, without inferring a new award.
        "recipient_crown_progress": _count(session, CrownProgress, CrownProgress.profile_id.in_(profiles)),
        "reward_inventory_placements": _count(session, RewardInventoryPlacement, or_(
            RewardInventoryPlacement.reward_grant_id.in_([g.id for g in grants]),
            RewardInventoryPlacement.crown_award_id.in_([c.id for c in crowns]),
        )),
        "placement_point_awards": _count(session, CampPointAward,
                                        CampPointAward.duplicate_key.like(f"contest:{week.id}:%")),
        "source_charts": _count(session, PracticeChart, PracticeChart.practice_date >= week.week_start,
                                PracticeChart.practice_date < week.week_end),
        "source_charts_with_team": _count(session, PracticeChart, PracticeChart.practice_date >= week.week_start,
                                          PracticeChart.practice_date < week.week_end, PracticeChart.team_id.is_not(None)),
        "source_point_awards_with_team": _count(session, CampPointAward, CampPointAward.occurred_at >= start,
                                                CampPointAward.occurred_at < end, CampPointAward.team_id.is_not(None)),
    }
    blockers = []
    team_ids = {r.team_id for r in results + snapshots if r.team_id is not None}
    for team_id in team_ids:
        team = session.get(Team, team_id)
        if team is None or target is None or team.season_id != target.id:
            blockers.append(f"week {week.id}: frozen team {team_id} does not belong to destination season")
    for snapshot in snapshots:
        if snapshot.membership_id is not None:
            member = session.get(TeamMembership, snapshot.membership_id)
            if (member is None or target is None or member.season_id != target.id
                    or member.team_id != snapshot.team_id or member.profile_id != snapshot.profile_id):
                blockers.append(f"week {week.id}: snapshot {snapshot.id} has incompatible membership")
    for result in results:
        contest = session.get(Contest, result.contest_id)
        if (contest is None or contest.key not in {c["key"] for c in CONTEST_DEFINITIONS}
                or result.subject_type != contest.subject_type
                or (result.subject_type == "team" and result.team_id is None)):
            blockers.append(f"week {week.id}: result {result.id} has ambiguous season/team semantics")
    # Source PracticeCharts and point ledgers retain the team at earning time.
    # They are not transplanted memberships, and are never rewritten here.
    return counts, blockers


def calendar_plan(session, *, repair=False):
    seasons = session.scalars(select(Season).order_by(Season.starts_on, Season.id)).all()
    by_key = {s.key: s for s in seasons}
    weeks = session.scalars(select(ContestWeek).order_by(ContestWeek.week_start, ContestWeek.id)).all()
    plan = {"mode": "repair" if repair else "bootstrap", "safe": False, "blockers": [],
            "create_seasons": [], "update_seasons": [], "reparent_weeks": [], "create_weeks": [],
            "seasons_before": [{"id": s.id, "key": s.key, "starts_on": s.starts_on.isoformat(),
                                "ends_on": s.ends_on.isoformat() if s.ends_on else None,
                                "status": s.status} for s in seasons],
            "preserved_band_camp": {}, "weeks_before": []}
    projected = {s.key: (s.starts_on, s.ends_on) for s in seasons}
    legacy = by_key.get(CANONICAL_SEASONS[0].key)
    for definition in CANONICAL_SEASONS:
        existing = by_key.get(definition.key)
        if existing is None:
            plan["create_seasons"].append(definition.key)
            projected[definition.key] = (definition.starts_on, definition.ends_on)
            continue
        if (existing.name, existing.starts_on, existing.ends_on, existing.timezone) == (
            definition.name, definition.starts_on, definition.ends_on, "America/Chicago",
        ):
            continue
        if (repair and existing is legacy and existing.ends_on is None and existing.status == "active"
                and (existing.name, existing.starts_on, existing.timezone)
                == (definition.name, definition.starts_on, "America/Chicago")):
            plan["update_seasons"].append({"id": existing.id, "key": existing.key,
                                            "ends_on": definition.ends_on.isoformat()})
            projected[definition.key] = (definition.starts_on, definition.ends_on)
        else:
            plan["blockers"].append(f"{definition.key}: existing calendar conflicts; no automatic overwrite")
    ranges = sorted((start, end, key) for key, (start, end) in projected.items())
    for left, right in zip(ranges, ranges[1:]):
        if left[1] is None or left[1] >= right[0]:
            plan["blockers"].append(f"overlap: {left[2]} / {right[2]}")
    # Fail closed if a later schema adds dependencies this repair has not audited.
    expected = {"seasons": {"teams", "team_memberships", "team_join_requests", "contest_weeks", "director_team_contests"},
                "contest_weeks": {"contest_results", "team_week_membership_snapshots"},
                "contest_results": {"reward_grants"}}
    inspector = inspect(session.connection())
    for table in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table):
            parent = fk["referred_table"]
            if parent in expected and table not in expected[parent]:
                plan["blockers"].append(f"unaudited dependency: {table} -> {parent}")
    seen = set()
    for week in weeks:
        owner = next(s for s in seasons if s.id == week.season_id)
        plan["weeks_before"].append({"id": week.id, "season": owner.key,
                                     "start": week.week_start.isoformat(), "end_exclusive": week.week_end.isoformat(),
                                     "status": week.status})
        if week.week_start in seen:
            plan["blockers"].append(f"duplicate week boundary: {week.week_start}")
        seen.add(week.week_start)
        if week.week_start.weekday() != 0 or week.week_end != week.week_start + timedelta(days=7):
            plan["blockers"].append(f"week {week.id}: invalid Monday/exclusive-Monday boundaries")
        start, end = projected[owner.key]
        if start <= week.week_start and (end is None or week.week_end <= end + timedelta(days=1)):
            continue
        destination = canonical_definition_for_date(week.week_start)
        if (repair and owner is legacy and destination is not None and destination.key != owner.key
                and (destination.ends_on is None or week.week_end <= destination.ends_on + timedelta(days=1))):
            dependencies, blockers = _dependencies(session, week, by_key.get(destination.key))
            plan["blockers"].extend(blockers)
            plan["reparent_weeks"].append({"id": week.id, "from": owner.key, "to": destination.key,
                                           "start": week.week_start.isoformat(), "dependencies": dependencies})
        else:
            plan["blockers"].append(f"week {week.id}: outside its season; explicit repair required")
    if repair:
        # Complete the approved Back-to-School season. Other seasons get their
        # current week lazily through the normal generic contest bootstrap.
        target = CANONICAL_SEASONS[1]
        start = target.starts_on
        while start <= target.ends_on:
            if start not in seen:
                plan["create_weeks"].append({"season": target.key, "start": start.isoformat()})
            start += timedelta(days=7)
    if legacy is not None:
        for model in (Team, TeamMembership, TeamJoinRequest, DirectorTeamContest):
            plan["preserved_band_camp"][model.__tablename__] = _count(session, model, model.season_id == legacy.id)
        cutoff = datetime.combine(CANONICAL_SEASONS[0].ends_on + timedelta(days=1), time.min, CENTRAL)
        for contest in session.scalars(select(DirectorTeamContest).where(DirectorTeamContest.season_id == legacy.id)):
            if aware_utc(contest.ends_at) > cutoff:
                plan["blockers"].append(f"director contest {contest.id}: immutable window extends beyond Band Camp")
    plan["safe"] = not plan["blockers"]
    return plan


def apply_calendar_plan(session, *, repair=False):
    """Re-plan under the caller's transaction; never trust a stale supplied plan."""
    plan = calendar_plan(session, repair=repair)
    if not plan["safe"]:
        raise SeasonConfigurationError("Calendar operation blocked: " + "; ".join(plan["blockers"]))
    for change in plan["update_seasons"]:
        session.get(Season, change["id"]).ends_on = CANONICAL_SEASONS[0].ends_on
    session.flush()
    bootstrap_canonical_seasons(session)
    by_key = {row.key: row for row in session.scalars(select(Season))}
    for change in plan["reparent_weeks"]:
        session.get(ContestWeek, change["id"]).season_id = by_key[change["to"]].id
    for change in plan["create_weeks"]:
        start = datetime.fromisoformat(change["start"]).date()
        end, deadline, finalizes = contest_week_schedule(start)
        session.add(ContestWeek(season_id=by_key[change["season"]].id, week_start=start, week_end=end,
                                verification_deadline_at=deadline, finalize_after=finalizes, status="open"))
    session.flush()
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("bootstrap", "repair"))
    parser.add_argument("--apply", action="store_true", help="Apply in one transaction; default is dry run")
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        try:
            if args.apply:
                # Lock before dependency inspection. Use during a maintenance
                # window; no web handler invokes this operation.
                if session.bind.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                elif session.bind.dialect.name == "postgresql":
                    session.execute(text("LOCK TABLE seasons, contest_weeks, contest_results, "
                                         "team_week_membership_snapshots, teams, team_memberships, "
                                         "director_team_contests, reward_grants, crown_awards, crown_progress, "
                                         "reward_inventory_placements, camp_point_awards, practice_charts "
                                         "IN SHARE ROW EXCLUSIVE MODE"))
                else:
                    raise SeasonConfigurationError("Unsupported repair database locking semantics.")
                report = apply_calendar_plan(session, repair=args.operation == "repair")
                session.commit()
            else:
                report = calendar_plan(session, repair=args.operation == "repair")
            print(json.dumps({**report, "applied": args.apply}, indent=2))
            return 0 if report["safe"] else 1
        except Exception as error:
            session.rollback()
            print(json.dumps({"applied": False, "error": str(error)}))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
