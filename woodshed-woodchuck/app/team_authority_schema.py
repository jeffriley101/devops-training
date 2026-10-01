"""Database interval guards shared by metadata fixtures and additive migration.

Legacy seasonal overlaps remain historical. Empty intervals are valid evidence
but never overlap an effective membership. Persistent mutations on PostgreSQL
require READ COMMITTED so the query after the advisory lock sees fresh rows.
"""

PG_FUNCTION = """
CREATE OR REPLACE FUNCTION enforce_persistent_team_membership_interval()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.is_persistent THEN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
      RAISE EXCEPTION 'persistent_team_membership_requires_read_committed' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(72020, NEW.profile_id);
    IF NEW.ended_at IS NOT NULL AND NEW.ended_at < NEW.started_at THEN
      RAISE EXCEPTION 'persistent_team_membership_negative_interval' USING ERRCODE='23514';
    END IF;
    IF (NEW.ended_at IS NULL OR NEW.started_at < NEW.ended_at) AND EXISTS (
      SELECT 1 FROM team_memberships m
      WHERE m.is_persistent AND m.profile_id = NEW.profile_id
        AND m.id <> COALESCE(NEW.id, -1)
        AND (m.ended_at IS NULL OR m.started_at < m.ended_at)
        AND m.started_at < COALESCE(NEW.ended_at, 'infinity'::timestamptz)
        AND NEW.started_at < COALESCE(m.ended_at, 'infinity'::timestamptz)
    ) THEN
      RAISE EXCEPTION 'persistent_team_membership_interval_overlap' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$
"""
PG_TRIGGER = """
CREATE TRIGGER persistent_team_membership_interval_guard
BEFORE INSERT OR UPDATE ON team_memberships
FOR EACH ROW EXECUTE FUNCTION enforce_persistent_team_membership_interval()
"""

# SQLAlchemy stores UTC DateTime values in fixed-width ISO text on SQLite.
# Compare that text directly: julianday() would discard submillisecond overlap.
SQLITE_BODY = """
WHEN NEW.is_persistent = true
BEGIN
  SELECT CASE WHEN NEW.ended_at IS NOT NULL
    AND NEW.ended_at < NEW.started_at
    THEN RAISE(ABORT, 'persistent_team_membership_negative_interval') END;
  SELECT CASE WHEN (NEW.ended_at IS NULL OR NEW.started_at < NEW.ended_at)
    AND EXISTS (
      SELECT 1 FROM team_memberships m
      WHERE m.is_persistent = true AND m.profile_id = NEW.profile_id
        AND m.id <> COALESCE(NEW.id, -1)
        AND (m.ended_at IS NULL OR m.started_at < m.ended_at)
        AND (NEW.ended_at IS NULL OR m.started_at < NEW.ended_at)
        AND (m.ended_at IS NULL OR NEW.started_at < m.ended_at)
    ) THEN RAISE(ABORT, 'persistent_team_membership_interval_overlap') END;
END
"""
SQLITE_INSERT = "CREATE TRIGGER persistent_team_membership_interval_insert BEFORE INSERT ON team_memberships " + SQLITE_BODY
SQLITE_UPDATE = "CREATE TRIGGER persistent_team_membership_interval_update BEFORE UPDATE ON team_memberships " + SQLITE_BODY


def install(connection):
    if connection.dialect.name == "postgresql":
        for statement in (PG_FUNCTION, PG_TRIGGER):
            connection.exec_driver_sql(statement)
    elif connection.dialect.name == "sqlite":
        for statement in (SQLITE_INSERT, SQLITE_UPDATE):
            connection.exec_driver_sql(statement)


def uninstall(connection):
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("DROP TRIGGER persistent_team_membership_interval_guard ON team_memberships")
        connection.exec_driver_sql("DROP FUNCTION enforce_persistent_team_membership_interval()")
    elif connection.dialect.name == "sqlite":
        connection.exec_driver_sql("DROP TRIGGER persistent_team_membership_interval_insert")
        connection.exec_driver_sql("DROP TRIGGER persistent_team_membership_interval_update")
