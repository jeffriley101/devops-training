"""Read-only Band Camp 2026 -> Back to School inventory. No repair entry point."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, create_engine, func, inspect, or_, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

BANNER = "READ-ONLY INVENTORY — NO REPAIR PERFORMED"
CENTRAL = ZoneInfo("America/Chicago")
SOURCE_START, SOURCE_END = date(2026, 7, 27), date(2026, 9, 13)
DEST_START, DEST_END = date(2026, 9, 14), date(2026, 9, 27)
BOUNDARY = datetime.combine(DEST_START, time.min, CENTRAL).astimezone(timezone.utc)
SOURCE_INSTANT = datetime.combine(SOURCE_START, time.min, CENTRAL).astimezone(timezone.utc)
END_INSTANT = datetime.combine(DEST_END + timedelta(days=1), time.min, CENTRAL).astimezone(timezone.utc)
KEYS = ("band-camp-2026", "back-to-school-2026")

# Only these columns can be selected into the report. No ORM or app.db imports.
FIELDS = {
    "alembic_version": "version_num",
    "seasons": "id key name starts_on ends_on status timezone created_at updated_at",
    "teams": "id season_id display_name normalized_name emblem_key creator_profile_id visibility director_led moderation_status moderation_updated_at created_at family_id",
    "team_families": "id",
    "team_memberships": "id profile_id team_id season_id started_at ended_at selected_week_start",
    "woodchuck_profiles": "id status",
    "profile_capabilities": "id profile_id capability",
    "team_join_requests": "id profile_id team_id season_id status requested_at resolved_at",
    "team_reports": "id team_id status category created_at resolved_at",
    "contest_weeks": "id season_id week_start week_end status finalized_at",
    "team_week_membership_snapshots": "id contest_week_id profile_id team_id membership_id snapshot_at",
    "practice_charts": "id profile_id practice_date team_id include_team_contests created_at",
    "camp_point_awards": "id profile_id occurred_at team_id created_at",
    "contest_results": "id contest_week_id team_id profile_id subject_type",
    "reward_grants": "id contest_result_id profile_id created_at",
    "crown_awards": "id profile_id earned_at created_at",
    "director_team_contests": "id season_id owner_profile_id status starts_at ends_at finalizes_at finalized_at",
    "director_team_contest_entries": "id contest_id team_id",
    "director_team_contest_results": "id contest_id team_id",
}
REQUIRED = {
    "seasons": "id key name starts_on ends_on status",
    "teams": "id season_id display_name normalized_name emblem_key creator_profile_id visibility director_led moderation_status",
    "team_memberships": FIELDS["team_memberships"],
}
# A missing optional section is unknown, never a false zero/safe conclusion.
SECTION_KEYS = {
    "alembic_version": "version_num",
    "team_families": "id",
    "contest_weeks": "id season_id week_start week_end status",
    "team_join_requests": "id profile_id team_id season_id status",
    "team_reports": "id team_id status",
    "woodchuck_profiles": "id status",
    "profile_capabilities": "id profile_id capability",
    "practice_charts": "id profile_id practice_date team_id",
    "camp_point_awards": "id profile_id occurred_at team_id",
    "contest_results": "id contest_week_id team_id",
    "team_week_membership_snapshots": "id contest_week_id team_id",
    "reward_grants": "id contest_result_id",
    "crown_awards": "id profile_id source_key",
    "director_team_contests": "id season_id owner_profile_id status starts_at ends_at",
    "director_team_contest_entries": "id contest_id team_id",
    "director_team_contest_results": "id contest_id team_id",
}


class InventoryError(Exception):
    """Only deliberately sanitized messages may be printed by the CLI."""


def verify_readonly(connection):
    if connection.dialect.name == "sqlite":
        valid = connection.scalar(text("PRAGMA query_only")) == 1
    elif connection.dialect.name == "postgresql":
        valid = (connection.scalar(text("SHOW transaction_read_only")) == "on"
                 and connection.scalar(text("SHOW transaction_isolation")) == "repeatable read")
    else:
        valid = False
    if not valid:
        raise InventoryError("Verified read-only snapshot required; no read/write fallback is permitted.")


def target_url(value):
    if not value:
        raise InventoryError("Supply --database-url or an explicit DATABASE_URL.")
    value = value.replace("postgres://", "postgresql+psycopg://", 1) if value.startswith("postgres://") else value
    value = value.replace("postgresql://", "postgresql+psycopg://", 1) if value.startswith("postgresql://") else value
    try:
        url = make_url(value)
    except Exception:
        raise InventoryError("Invalid database URL; connection details withheld.") from None
    if url.drivername not in {"sqlite", "sqlite+pysqlite", "postgresql+psycopg"}:
        raise InventoryError("Only file SQLite and PostgreSQL/Psycopg connections are supported.")
    if url.get_backend_name() == "sqlite" and (not url.database or url.database == ":memory:" or url.query):
        raise InventoryError("SQLite requires an existing file URL without query parameters.")
    if url.get_backend_name() == "postgresql":
        if not url.host or not url.database:
            raise InventoryError("PostgreSQL URL must explicitly identify its host and database.")
        allowed_options = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout",
                           "options", "application_name", "channel_binding", "gssencmode"}
        if set(url.query) - allowed_options:
            raise InventoryError("Unsupported PostgreSQL URL options; put target and credentials in URL authority/path.")
    return url


def target_metadata(url):
    return {"dialect": url.get_backend_name(),
            "host": url.host if url.get_backend_name() == "postgresql" else None,
            "port": url.port, "database": url.database}


@contextmanager
def readonly_connection(value):
    """All transactions are read-only; always rollback, including on errors.

    Psycopg's transaction characteristics are set BEFORE SQLAlchemy receives the
    connection, covering dialect initialization too. No write-mode fallback.
    SQLite mode=ro prevents creation/write of the file; query_only blocks temp
    writes too. Repeatable snapshots keep the sections internally comparable.
    """
    url = target_url(value)
    if url.get_backend_name() == "sqlite":
        path = Path(url.database).expanduser().resolve(strict=True)
        if not path.is_file():
            raise InventoryError("SQLite target must be an existing regular database file.")
        def connect():
            raw = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            raw.execute("PRAGMA query_only=ON")
            return raw
        engine = create_engine("sqlite://", creator=connect, poolclass=NullPool)
    else:
        import psycopg
        params = url.translate_connect_args(username="user", database="dbname")
        # Support standard TLS/search_path options without logging any values.
        params.update(dict(url.query))
        def connect():
            raw = psycopg.connect(**params)
            raw.read_only = True
            raw.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            return raw
        engine = create_engine("postgresql+psycopg://", creator=connect, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            try:
                if url.get_backend_name() == "sqlite":
                    verify_readonly(connection)
                    connection.exec_driver_sql("BEGIN")
                else:
                    verify_readonly(connection)
                yield connection
            finally:
                connection.rollback()
    finally:
        engine.dispose()


def utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def day(value):
    if value is None:
        return None
    return date.fromisoformat(value) if isinstance(value, str) else value


def json_value(value):
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("Unsupported inventory value")


class Schema:
    def __init__(self, connection):
        self.connection = connection
        inspector = inspect(connection)
        present = set(inspector.get_table_names())
        self.tables, self.description = {}, {}
        for name, fields in FIELDS.items():
            columns = set()
            if name in present:
                columns = {c["name"] for c in inspector.get_columns(name)}
            expected = set(fields.split()) - ({"family_id"} if name == "teams" else set())
            missing = sorted(expected - columns)
            self.description[name] = {"present": name in present, "columns": sorted(columns),
                                      "unavailable_columns": missing}
            if name in REQUIRED and (name not in present or set(REQUIRED[name].split()) - columns):
                raise InventoryError("Required pre-H1A schema missing: " + name + " / " +
                                     ", ".join(sorted(set(REQUIRED[name].split()) - columns)))
            if name in present and not (set(SECTION_KEYS.get(name, "").split()) - columns):
                self.tables[name] = Table(name, MetaData(), autoload_with=connection, resolve_fks=False)
            self.description[name]["section_available"] = name in self.tables
        family_table = "team_families" in self.tables
        family_column = "family_id" in self.tables["teams"].c
        self.mode = "team_family_available" if family_table and family_column else "pre_team_family"
        self.partial_family_schema = family_table != family_column

    def read(self, name, where=None, fields=None):
        if name not in self.tables:
            return []
        table = self.tables[name]
        selected = [table.c[f] for f in (fields or FIELDS[name].split()) if f in table.c]
        if name == "teams" and fields is None and "join_code" in table.c:
            selected.append((table.c.join_code.is_not(None) & (table.c.join_code != "")).label("join_code_present"))
        statement = select(*selected)
        if where is not None:
            statement = statement.where(where(table))
        statement = statement.order_by(table.c.id if "id" in table.c else table.c.version_num)
        return [dict(r) for r in self.connection.execute(statement).mappings()]

    def team_counts(self, name, team_ids, condition=None):
        if name not in self.tables or "team_id" not in self.tables[name].c:
            return None
        table = self.tables[name]
        statement = select(table.c.team_id, func.count()).where(table.c.team_id.in_(team_ids))
        if condition is not None:
            statement = statement.where(condition(table))
        return dict(self.connection.execute(statement.group_by(table.c.team_id)).all())


def local_commit():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=3, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_report(connection, *, now=None, commit=None):
    verify_readonly(connection)
    schema = Schema(connection)
    seasons = schema.read("seasons")
    source = next((s for s in seasons if s["key"] == KEYS[0]), None)
    dest = next((s for s in seasons if s["key"] == KEYS[1]), None)
    season_reasons = []
    for row, start, end, label in ((source, SOURCE_START, SOURCE_END, "source"),
                                  (dest, DEST_START, DEST_END, "destination")):
        if row is None:
            season_reasons.append(label + "_season_missing")
        elif day(row["starts_on"]) != start or day(row["ends_on"]) != end or row.get("timezone") != "America/Chicago":
            season_reasons.append(label + "_calendar_mismatch")
    source_id, dest_id = (source["id"] if source else None), (dest["id"] if dest else None)
    relevant_ids = [i for i in (source_id, dest_id) if i is not None]
    covering = [s["id"] for s in seasons if day(s["starts_on"]) <= DEST_START
                and (s["ends_on"] is None or day(s["ends_on"]) >= DEST_START) and s["status"] == "active"]
    if covering != ([dest_id] if dest_id is not None else []):
        season_reasons.append("boundary_date_coverage_mismatch")
    teams = schema.read("teams", lambda t: t.c.season_id.in_(relevant_ids))
    team_ids = [t["id"] for t in teams]
    sources = [t for t in teams if t["season_id"] == source_id]
    destinations = [t for t in teams if t["season_id"] == dest_id]
    members = schema.read("team_memberships", lambda t: or_(t.c.season_id.in_(relevant_ids), t.c.team_id.in_(team_ids)))
    requests = schema.read("team_join_requests", lambda t: or_(t.c.season_id.in_(relevant_ids), t.c.team_id.in_(team_ids)))
    reports = schema.read("team_reports", lambda t: t.c.team_id.in_(team_ids))
    charts = schema.read("practice_charts", lambda t: t.c.practice_date.between(SOURCE_START, DEST_END))
    awards = schema.read("camp_point_awards", lambda t: (t.c.occurred_at >= SOURCE_INSTANT) & (t.c.occurred_at < END_INSTANT))
    referenced_ids = {r["team_id"] for r in members + requests + charts + awards if r.get("team_id") is not None}
    team_map = {t["id"]: t for t in teams}
    for t in schema.read("teams", lambda t: t.c.id.in_(referenced_ids - set(team_map)), fields=["id", "season_id"]):
        team_map[t["id"]] = t
    integrity_issues = [{"table": table, "id": row["id"], "team_id": row["team_id"],
                         "season_id": row["season_id"], "reason": "season_team_reference_mismatch"}
                        for table, rows in (("team_memberships", members), ("team_join_requests", requests))
                        for row in rows if team_map.get(row["team_id"], {}).get("season_id") != row["season_id"]]
    profiles = {r["id"]: r for r in schema.read("woodchuck_profiles", lambda t: t.c.id.in_(
        {r["profile_id"] for r in members + requests} | {t["creator_profile_id"] for t in teams if t["creator_profile_id"]}))}
    capabilities = {r["profile_id"] for r in schema.read("profile_capabilities", lambda t:
        t.c.profile_id.in_(profiles) & (t.c.capability == "band_director"))}
    weeks = schema.read("contest_weeks", lambda t: t.c.season_id.in_(relevant_ids))
    week_ids = [w["id"] for w in weeks]
    dest_week_ids = {w["id"] for w in weeks if w["season_id"] == dest_id}
    results = schema.read("contest_results", lambda t: t.c.contest_week_id.in_(week_ids))
    snapshots = schema.read("team_week_membership_snapshots", lambda t: t.c.contest_week_id.in_(week_ids))
    grants = schema.read("reward_grants", lambda t: t.c.contest_result_id.in_([r["id"] for r in results]))
    crowns = []
    crown_link_available = "reward_grants" in schema.tables and {"source_key", "profile_id"} <= set(schema.tables["reward_grants"].c.keys())
    if crown_link_available:
        grant_table = schema.tables["reward_grants"]
        crowns = schema.read("crown_awards", lambda t: select(grant_table.c.id).where(
            grant_table.c.source_key == t.c.source_key, grant_table.c.profile_id == t.c.profile_id,
            grant_table.c.contest_result_id.in_([r["id"] for r in results])).exists())
    director_entries = schema.read("director_team_contest_entries", lambda t: t.c.team_id.in_(team_ids))
    director_results = schema.read("director_team_contest_results", lambda t: t.c.team_id.in_(team_ids))
    director_ids = {r["contest_id"] for r in director_entries + director_results}
    director = schema.read("director_team_contests", lambda t: or_(t.c.season_id.in_(relevant_ids),
        t.c.id.in_(director_ids), (t.c.starts_at <= END_INSTANT) & (t.c.ends_at >= BOUNDARY)))
    # Include all entries/results for the selected contests, even unusual team references.
    director_entries = schema.read("director_team_contest_entries", lambda t: t.c.contest_id.in_([d["id"] for d in director]))
    director_results = schema.read("director_team_contest_results", lambda t: t.c.contest_id.in_([d["id"] for d in director]))
    for d in director:
        d["entry_team_ids"] = [e["team_id"] for e in director_entries if e["contest_id"] == d["id"]]
        d["result_ids"] = [r["id"] for r in director_results if r["contest_id"] == d["id"]]
        d["touches_boundary"] = utc(d["starts_at"]) <= BOUNDARY <= utc(d["ends_at"])
        d["backdating_review"] = d["touches_boundary"] or d["season_id"] == dest_id
    frozen_team_ids = {r["team_id"] for r in results + snapshots + director_entries + director_results if r.get("team_id") is not None}
    for t in schema.read("teams", lambda t: t.c.id.in_(frozen_team_ids - set(team_map)), fields=["id", "season_id"]):
        team_map[t["id"]] = t

    def attribution(row, observed_day):
        expected = source_id if SOURCE_START <= observed_day <= SOURCE_END else dest_id
        actual = team_map.get(row["team_id"], {}).get("season_id")
        reasons = []
        if row["team_id"] is None:
            reasons.append("null_team_attribution")
        elif actual is None:
            reasons.append("missing_team_reference")
        elif actual != expected:
            reasons.append("cross_season_team_attribution")
        return {**row, "expected_season_id": expected, "team_season_id": actual, "reasons": reasons}

    chart_attribution = []
    for row in charts:
        observed = day(row["practice_date"])
        late = observed <= SOURCE_END and row.get("created_at") is not None and utc(row["created_at"]) >= BOUNDARY
        if observed >= DEST_START or late:
            item = attribution(row, observed)
            if late:
                item["reasons"].append("late_submitted_source_chart")
            item["late_submitted_source_chart"] = late
            chart_attribution.append(item)
    award_attribution = [attribution(row, utc(row["occurred_at"]).astimezone(CENTRAL).date())
                         for row in awards if utc(row["occurred_at"]) >= BOUNDARY]

    source_team_ids = {t["id"] for t in sources}
    source_members = [m for m in members if m["season_id"] == source_id or m["team_id"] in source_team_ids]
    dest_team_ids = {t["id"] for t in destinations}
    dest_members = [m for m in members if m["season_id"] == dest_id or m["team_id"] in dest_team_ids]
    boundary_candidates = [m for m in source_members if utc(m["started_at"]) < BOUNDARY
                           and (m["ended_at"] is None or utc(m["ended_at"]) >= BOUNDARY)]
    roster = []
    for m in source_members:
        exact = m["ended_at"] is not None and utc(m["ended_at"]) == BOUNDARY
        active = utc(m["started_at"]) < BOUNDARY and (m["ended_at"] is None or utc(m["ended_at"]) > BOUNDARY)
        overlap = sum(r["profile_id"] == m["profile_id"] for r in boundary_candidates) > 1 and m in boundary_candidates
        roster.append({**m, "active_immediately_before_boundary": active, "ended_exactly_at_boundary": exact,
                       "overlapping_boundary_memberships": overlap,
                       "season_matches_team": team_map.get(m["team_id"], {}).get("season_id") == m["season_id"],
                       "profile_status": profiles.get(m["profile_id"], {}).get("status")})
    active_profiles = {m["profile_id"] for m in roster if m["active_immediately_before_boundary"] or m["ended_exactly_at_boundary"]}
    destination_activity = []
    for pid in sorted(active_profiles):
        destination_activity.append({"profile_id": pid,
            "memberships": [m for m in dest_members if m["profile_id"] == pid],
            "requests": [r for r in requests if r["season_id"] == dest_id and r["profile_id"] == pid],
            "created_public_team_ids": [t["id"] for t in destinations if t["visibility"] == "public" and t["creator_profile_id"] == pid],
            "chart_ids": [r["id"] for r in chart_attribution if r["profile_id"] == pid and day(r["practice_date"]) >= DEST_START],
            "award_ids": [r["id"] for r in award_attribution if r["profile_id"] == pid]})

    unavailable = sorted(n for n in SECTION_KEYS if n not in {"team_families", "alembic_version"}
                         and not schema.description[n]["section_available"])
    if not crown_link_available:
        unavailable.append("crown_award_result_link")
    missing_optional_columns = {n: d["unavailable_columns"] for n, d in schema.description.items()
                                if n not in {"team_families", "alembic_version"} and d["unavailable_columns"]}
    schema_incomplete = bool(unavailable or missing_optional_columns or schema.partial_family_schema
                             or "join_code" not in schema.tables["teams"].c)
    frozen = []
    for name, rows in (("contest_results", results), ("team_week_membership_snapshots", snapshots), ("reward_grants", grants)):
        for row in rows:
            week_id = row.get("contest_week_id")
            if name == "reward_grants":
                week_id = next((r["contest_week_id"] for r in results if r["id"] == row["contest_result_id"]), None)
            frozen.append({"table": name, **row, "destination": week_id in dest_week_ids})
            if "team_id" in row:
                expected_season = next((w["season_id"] for w in weeks if w["id"] == week_id), None)
                actual_season = team_map.get(row["team_id"], {}).get("season_id")
                frozen[-1].update({"team_season_id": actual_season,
                    "team_attribution": "null" if row["team_id"] is None else "missing_reference" if actual_season is None
                    else "matching" if actual_season == expected_season else "cross_season"})
    # Match crown linkage inside SQL; never emit source_key (which can embed IDs).
    if crowns:
        destination_result_ids = [r["id"] for r in results if r["contest_week_id"] in dest_week_ids]
        destination_crowns = {r["id"] for r in schema.read("crown_awards", lambda t: select(grant_table.c.id).where(
            grant_table.c.source_key == t.c.source_key, grant_table.c.profile_id == t.c.profile_id,
            grant_table.c.contest_result_id.in_(destination_result_ids)).exists())}
        frozen.extend({"table": "crown_awards", **row, "destination": row["id"] in destination_crowns} for row in crowns)
    for d in director:
        for result_id in d["result_ids"]:
            frozen.append({"table": "director_team_contest_results", "id": result_id,
                           "contest_id": d["id"], "destination": d["season_id"] == dest_id or d["touches_boundary"]})
    frozen_weeks = [w for w in weeks if w["id"] in dest_week_ids and (w["status"] == "finalized" or w.get("finalized_at"))]
    frozen_destination = bool(frozen_weeks or any(r["destination"] for r in frozen))
    for w in weeks:
        w["artifact_counts"] = {
            name: sum(r["contest_week_id"] == w["id"] for r in rows) if name in schema.tables else None
            for name, rows in (("contest_results", results), ("team_week_membership_snapshots", snapshots))}
        w["reward_grant_count"] = sum(g["contest_result_id"] in {r["id"] for r in results if r["contest_week_id"] == w["id"]}
                                      for g in grants) if "reward_grants" in schema.tables and "contest_results" in schema.tables else None

    collisions = []
    for old in sources:
        for new in destinations:
            reasons = [code for field, code in (("normalized_name", "name_collision"), ("emblem_key", "emblem_collision"))
                       if old[field] == new[field]]
            if old["visibility"] == new["visibility"] == "public" and old["creator_profile_id"] is not None and old["creator_profile_id"] == new["creator_profile_id"]:
                reasons.append("public_creator_collision")
            same_family = schema.mode == "team_family_available" and old.get("family_id") is not None and old["family_id"] == new.get("family_id")
            if same_family:
                reasons.append("same_family")
            if not reasons:
                continue
            mismatch = old["visibility"] != new["visibility"] or old["director_led"] != new["director_led"]
            if mismatch:
                reasons.append("team_type_mismatch")
            if old["moderation_status"] != new["moderation_status"]:
                reasons.append("moderation_mismatch")
            contradictory = same_family and (mismatch or any(old[f] != new[f] for f in
                ("display_name", "normalized_name", "emblem_key", "creator_profile_id", "moderation_status")))
            manual = not same_family and sum(old[f] == new[f] and old[f] is not None for f in
                ("normalized_name", "emblem_key", "creator_profile_id")) >= 2
            classification = "CONFLICT" if contradictory or mismatch else "INFORMATIONAL" if same_family else "REVIEW" if manual else "CONFLICT"
            if contradictory:
                reasons.append("same_family_successor_mismatch")
            if manual:
                reasons.append("possible_manual_successor")
            collisions.append({"source_team_id": old["id"], "destination_team_id": new["id"],
                "classification": classification, "reasons": reasons,
                "label": "POSSIBLE MANUAL SUCCESSOR — REVIEW REQUIRED" if manual else None})

    member_classifications = []
    for m in roster:
        reasons = []
        state = "INFORMATIONAL"
        if not m["season_matches_team"]:
            state, reasons = "CONFLICT", ["membership_season_mismatch"]
        elif m["active_immediately_before_boundary"] or m["ended_exactly_at_boundary"]:
            state = "SAFE_CANDIDATE"
            source_team = team_map.get(m["team_id"], {})
            owner_id = source_team.get("creator_profile_id")
            private_ineligible = source_team.get("visibility") == "private" and (
                not source_team.get("director_led") or owner_id not in capabilities
                or profiles.get(owner_id, {}).get("status") != "active")
            if any(r["profile_id"] == m["profile_id"] for r in dest_members):
                state = "CONFLICT"; reasons.append("destination_membership_history")
            if frozen_destination:
                state = "CONFLICT"; reasons.append("destination_frozen")
            for flag, code in ((m["ended_exactly_at_boundary"], "exact_boundary_ending"),
                               (source_team.get("moderation_status") != "active", "source_not_active"),
                               (private_ineligible, "private_owner_ineligible_or_unknown"),
                               (m["overlapping_boundary_memberships"], "overlapping_source_memberships"),
                               (m["profile_status"] != "active", "student_ineligible_or_unknown"),
                               (schema_incomplete, "incomplete_inventory"), (bool(season_reasons), "season_authority_review"),
                               (any(d["backdating_review"] for d in director), "director_contest_review"),
                               (any(r["profile_id"] == m["profile_id"] and r["reasons"] for r in chart_attribution + award_attribution), "attribution_review")):
                if flag:
                    reasons.append(code)
                    if state != "CONFLICT":
                        state = "REVIEW"
        else:
            reasons.append("not_boundary_member")
        member_classifications.append({"membership_id": m["id"], "profile_id": m["profile_id"], "team_id": m["team_id"],
                                       "classification": state, "reasons": reasons or ["qualifying_boundary_roster"]})

    counts_by_team = {}
    for name in ("team_memberships", "team_join_requests", "team_reports", "practice_charts", "camp_point_awards",
                 "contest_results", "team_week_membership_snapshots", "director_team_contest_entries", "director_team_contest_results"):
        condition = (lambda t: t.c.status == "pending") if name == "team_join_requests" else (
            (lambda t: t.c.status == "unresolved") if name == "team_reports" else None)
        counts_by_team[name] = schema.team_counts(name, team_ids, condition)
    for t in teams:
        labels = {"team_memberships": "memberships_total", "team_join_requests": "pending_requests", "team_reports": "unresolved_reports"}
        t["counts"] = {labels.get(name, name): values.get(t["id"], 0) if values is not None else None for name, values in counts_by_team.items()}
        relevant = [m for m in members if m["team_id"] == t["id"]]
        t["counts"].update({"ended_memberships": sum(m["ended_at"] is not None for m in relevant),
            "active_boundary_memberships": sum(utc(m["started_at"]) < BOUNDARY and (m["ended_at"] is None or utc(m["ended_at"]) > BOUNDARY) for m in relevant)})
        t.setdefault("join_code_present", None)
    private = []
    team_classifications = []
    family_ids = {r["id"] for r in schema.read("team_families", lambda t: t.c.id.in_(
        {team.get("family_id") for team in teams}))} if schema.mode == "team_family_available" else set()
    for t in sources:
        review, conflict = [], []
        ms = [m for m in member_classifications if m["team_id"] == t["id"]]
        cs = [c for c in collisions if c["source_team_id"] == t["id"]]
        if t["moderation_status"] != "active":
            review.append("source_not_active")
        if t["visibility"] == "public" and not any(m["classification"] == "SAFE_CANDIDATE" for m in ms):
            review.append("no_safe_boundary_roster")
        if t["creator_profile_id"] is not None and profiles.get(t["creator_profile_id"], {}).get("status") != "active":
            review.append("creator_ineligible_or_unknown")
        if t["visibility"] == "private" or t["director_led"]:
            owner_status = profiles.get(t["creator_profile_id"], {}).get("status")
            capable = t["creator_profile_id"] in capabilities if "profile_capabilities" in schema.tables else None
            if owner_status != "active" or not capable or not t["director_led"] or t["visibility"] != "private":
                review.append("private_owner_ineligible_or_unknown")
            private.append({"team_id": t["id"], "owner_profile_id": t["creator_profile_id"], "owner_status": owner_status,
                "band_director_capability": capable, "active_boundary_roster_count": t["counts"]["active_boundary_memberships"],
                "pending_request_count": t["counts"]["pending_requests"], "join_code_present": t["join_code_present"],
                "destination_owned_private_team_ids": [n["id"] for n in destinations if t["creator_profile_id"] is not None and n["creator_profile_id"] == t["creator_profile_id"]
                                                       and (n["visibility"] == "private" or n["director_led"])],
                "collision_destination_ids": [c["destination_team_id"] for c in cs]})
        if schema.mode == "team_family_available" and t.get("family_id") not in family_ids:
            conflict.append("missing_family_reference")
        if schema_incomplete: review.append("incomplete_inventory")
        if season_reasons: review.append("season_authority_review")
        if any(d["backdating_review"] for d in director): review.append("director_contest_review")
        if dest and dest["status"] == "closed": review.append("destination_closed")
        if frozen_destination: conflict.append("destination_frozen")
        if integrity_issues: conflict.append("season_team_reference_mismatch")
        for c in cs:
            if c["classification"] == "CONFLICT": conflict.extend(c["reasons"])
            elif c["classification"] == "REVIEW": review.extend(c["reasons"])
        for m in ms:
            if m["classification"] == "CONFLICT": conflict.extend(m["reasons"])
            elif m["classification"] == "REVIEW": review.extend(m["reasons"])
        team_classifications.append({"team_id": t["id"],
            "classification": "CONFLICT" if conflict else "REVIEW" if review else "SAFE_CANDIDATE",
            "reasons": sorted(set(conflict + review)) or ["eligible_continuation_candidate"]})

    pending = [{**r, "classification": "REVIEW", "reasons": ["pending_request_not_continued"],
                "destination_membership_ids": [m["id"] for m in dest_members if m["profile_id"] == r["profile_id"]],
                "destination_request_ids": [d["id"] for d in requests if d["season_id"] == dest_id and d["profile_id"] == r["profile_id"]]}
               for r in requests if r["season_id"] == source_id and r["status"] == "pending"]
    unavailable_count = lambda table, number: number if table in schema.tables else None
    summary = {
        "band_camp_teams": len(sources), "back_to_school_teams": len(destinations),
        "active_boundary_memberships": sum(m["active_immediately_before_boundary"] for m in roster),
        "exact_boundary_endings": sum(m["ended_exactly_at_boundary"] for m in roster),
        "students_with_destination_membership_history": len({m["profile_id"] for m in dest_members if m["profile_id"] in active_profiles}),
        "possible_manual_successor_teams": len({c["destination_team_id"] for c in collisions if "possible_manual_successor" in c["reasons"]}),
        "hidden_or_review_teams": sum(t["moderation_status"] != "active" for t in teams),
        "pending_source_requests": unavailable_count("team_join_requests", len(pending)),
        "pending_source_private_requests": unavailable_count("team_join_requests", sum(
            team_map.get(r["team_id"], {}).get("visibility") == "private" for r in pending)),
        "source_private_director_teams": len(private),
        "destination_finalized_weeks": unavailable_count("contest_weeks", len(frozen_weeks)),
        "destination_frozen_artifacts": sum(r["destination"] for r in frozen),
        "suspicious_chart_rows": unavailable_count("practice_charts", sum(bool(r["reasons"]) for r in chart_attribution)),
        "suspicious_award_rows": unavailable_count("camp_point_awards", sum(bool(r["reasons"]) for r in award_attribution)),
        "team_classifications": dict(sorted(Counter(r["classification"] for r in team_classifications).items())),
        "member_classifications": dict(sorted(Counter(r["classification"] for r in member_classifications).items())),
        "inventory_complete": not schema_incomplete,
        "repair_authorized": False,
        "team_types_by_season": {label: {"public": sum(t["visibility"] == "public" for t in rows),
            "private": sum(t["visibility"] == "private" for t in rows), "director_led": sum(bool(t["director_led"]) for t in rows)}
            for label, rows in (("source", sources), ("destination", destinations))},
    }
    summary["repair_assessment"] = ("INCOMPLETE_INVENTORY" if schema_incomplete or season_reasons else
        "CONFLICTS_OBSERVED" if any(t["classification"] == "CONFLICT" for t in team_classifications) else
        "REVIEW_REQUIRED" if any(t["classification"] == "REVIEW" for t in team_classifications) else
        "CANDIDATES_REQUIRE_OPERATOR_REVIEW")
    report = {
        "metadata": {"notice": BANNER, "inventory_timestamp": now or datetime.now(timezone.utc),
                     "local_git_commit": commit, "read_only": True, "boundary_utc": BOUNDARY,
                     "naive_timestamps_interpreted_as": "UTC", "alembic_revisions": [r["version_num"] for r in schema.read("alembic_version")]},
        "schema": {"schema_mode": schema.mode, "partial_family_schema": schema.partial_family_schema,
                   "tables": schema.description, "unavailable_sections": unavailable},
        "seasons": {"source": source, "destination": dest, "validation_reasons": season_reasons, "active_covering_boundary_ids": covering},
        "weeks": weeks, "source_teams": sources, "destination_teams": destinations,
        "boundary_roster": roster, "destination_activity": destination_activity,
        "integrity_issues": integrity_issues,
        "team_classifications": team_classifications, "member_classifications": member_classifications,
        "collisions": collisions, "private_director_teams": private, "pending_requests": pending,
        "moderation": {"teams": [t["id"] for t in teams if t["moderation_status"] != "active"], "reports": reports},
        "director_contests": director, "chart_attribution": chart_attribution, "award_attribution": award_attribution,
        "frozen_dependencies": frozen, "summary_counts": summary,
    }
    # Normalize at the API boundary; stable ordering and explicit time formatting.
    return json.loads(json.dumps(report, default=json_value, sort_keys=True))


def inventory(value, *, now=None, commit=None):
    url = target_url(value)
    with readonly_connection(value) as connection:
        report = build_report(connection, now=now, commit=commit)
        report["metadata"]["target"] = target_metadata(url)
        if url.get_backend_name() == "postgresql":
            report["metadata"]["server"] = dict(connection.execute(text(
                "SELECT current_database() AS database, inet_server_addr()::text AS address, "
                "inet_server_port() AS port, current_schema() AS schema")).mappings().one())
    return report


def human_summary(report):
    c = report["summary_counts"]
    labels = {
        "band_camp_teams": "Band Camp Teams", "back_to_school_teams": "Back-to-School Teams",
        "active_boundary_memberships": "Confirmed active boundary memberships",
        "exact_boundary_endings": "Exact-boundary endings requiring review",
        "students_with_destination_membership_history": "Students with destination membership history",
        "possible_manual_successor_teams": "Possible manually recreated successor Teams",
        "hidden_or_review_teams": "Hidden/under-review Teams", "pending_source_requests": "Pending source requests",
        "pending_source_private_requests": "Pending source private requests",
        "source_private_director_teams": "Source private/director Teams",
        "destination_finalized_weeks": "Destination finalized weeks", "destination_frozen_artifacts": "Observed destination frozen artifacts",
        "suspicious_chart_rows": "Suspicious chart rows", "suspicious_award_rows": "Suspicious award rows",
    }
    lines = [BANNER, "Schema: " + report["schema"]["schema_mode"]]
    lines += [f"{label}: {c[key] if c[key] is not None else 'UNAVAILABLE'}" for key, label in labels.items()]
    for kind in ("team", "member"):
        counts = c[kind + "_classifications"]
        lines.append(kind.title() + " classifications: " + ", ".join(
            f"{state}={counts.get(state, 0)}" for state in ("SAFE_CANDIDATE", "REVIEW", "CONFLICT", "INFORMATIONAL")))
    lines.append("Inventory complete: " + str(c["inventory_complete"]))
    lines.append("Repair assessment: " + c["repair_assessment"] + "; candidates never authorize repair.")
    if not c["inventory_complete"]:
        lines.append("WARNING: unavailable data prevents a complete safety assessment; see JSON schema sections.")
    if report["seasons"]["validation_reasons"]:
        lines.append("Season validation: " + ", ".join(report["seasons"]["validation_reasons"]))
    lines.append("NO REPAIR WAS PERFORMED.")
    return "\n".join(lines)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse otherwise echoes unknown arguments, potentially including a URL.
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid inventory arguments; use --help. No connection details printed.\n")


def main(argv=None):
    parser = SafeArgumentParser(description=BANNER)
    parser.add_argument("--database-url", help="Explicit target URL; alternatively set DATABASE_URL (recommended for secrets).")
    parser.add_argument("--output", help="Create a NEW JSON report at this explicit path; never overwrite an existing file.")
    parser.add_argument("--format", choices=("both", "json", "summary"), default="both",
                        help="Default: human summary on stderr, JSON on stdout (or --output).")
    args = parser.parse_args(argv)
    value = args.database_url or os.environ.get("DATABASE_URL")
    try:
        url = target_url(value)
        print(BANNER, file=sys.stderr)
        print("Target: " + json.dumps(target_metadata(url), sort_keys=True), file=sys.stderr)
        report = inventory(value, commit=local_commit())
        if args.format in {"both", "summary"}:
            print(human_summary(report), file=sys.stderr)
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            # Exclusive creation also prevents accidentally overwriting the DB.
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
        elif args.format in {"both", "json"}:
            print(encoded, end="")
        return 0
    except InventoryError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        # Driver errors may embed credentials, notes or SQL parameters. Never
        # print them or a traceback. The operator gets a deliberate safe error.
        print("Inventory failed; verify target, read-only permissions, schema, and output path. Details withheld.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
