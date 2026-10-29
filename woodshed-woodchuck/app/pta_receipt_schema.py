"""Frozen p31 issuance storage and guards; no historical provenance backfill.

PTA has one authority per database/schema, not one per Classroom Program. The
target digest and singleton foreign key bind that existing authority scope.
Database owners who can replace DDL are outside the application trust boundary;
operators validate the actual constraints and guard definitions before use.
"""
from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKey, Integer,
                        String, Table, UniqueConstraint, text)

REVISION = "p31pta001"
CONTRACT = "pta-w25weekly005-v1"
NAME = "persistent_team_receipt_issuances"
HASHES = ("target_sha256", "plan_sha256", "authority_sha256", "receipt_sha256")
CHECKS = {
    "ck_pta_receipt_singleton": "control_id = 1",
    "ck_pta_receipt_id": "length(receipt_id) = 32",
    "ck_pta_receipt_revision": "activation_revision = 'p31pta001'",
    "ck_pta_receipt_contract": "evidence_contract = 'pta-w25weekly005-v1'",
    "ck_pta_receipt_time": "issued_at >= effective_at",
    **{"ck_pta_receipt_" + name: "length(" + name + ") = 64" for name in HASHES},
}


def table(metadata):
    return Table(NAME, metadata,
        Column("receipt_id", String(32), primary_key=True),
        Column("control_id", Integer, ForeignKey("persistent_team_control.id", ondelete="RESTRICT"), nullable=False),
        *(Column(name, String(64), nullable=False) for name in HASHES),
        Column("activation_revision", String(32), nullable=False),
        Column("evidence_contract", String(40), nullable=False),
        Column("effective_at", DateTime(timezone=True), nullable=False),
        Column("issued_at", DateTime(timezone=True), nullable=False),
        *(CheckConstraint(sql, name=name) for name, sql in CHECKS.items()),
        UniqueConstraint("control_id", name="uq_pta_receipt_control"),
        UniqueConstraint("plan_sha256", name="uq_pta_receipt_plan"),
        UniqueConstraint("receipt_sha256", name="uq_pta_receipt_digest"))


P31 = {NAME: {
    "exact": True,
    "columns": {"receipt_id": ("String", 32, False), "control_id": ("Integer", None, False),
        **{name: ("String", 64, False) for name in HASHES},
        "activation_revision": ("String", 32, False), "evidence_contract": ("String", 40, False),
        "effective_at": ("DateTime", None, False), "issued_at": ("DateTime", None, False)},
    "defaults": {}, "checks": CHECKS,
    "pg_checks": {**CHECKS,
        "ck_pta_receipt_id": "length(receipt_id::text) = 32",
        "ck_pta_receipt_revision": "activation_revision::text = 'p31pta001'::text",
        "ck_pta_receipt_contract": "evidence_contract::text = 'pta-w25weekly005-v1'::text",
        **{"ck_pta_receipt_" + name: "length(" + name + "::text) = 64" for name in HASHES}},
    "primary_key": ("receipt_id",),
    "unique": {"uq_pta_receipt_control": ("control_id",), "uq_pta_receipt_plan": ("plan_sha256",),
               "uq_pta_receipt_digest": ("receipt_sha256",)},
    "foreign_keys": [(("control_id",), "persistent_team_control", ("id",), "RESTRICT")],
    "indexes": {},
}}

SQLITE_INSERT = """CREATE TRIGGER pta_receipt_insert BEFORE INSERT ON persistent_team_receipt_issuances
BEGIN
  SELECT CASE WHEN EXISTS (SELECT 1 FROM persistent_team_receipt_issuances)
    THEN RAISE(ABORT, 'pta_receipt_already_issued') END;
  SELECT CASE WHEN (SELECT count(*) FROM alembic_version) <> 1
    OR (SELECT version_num FROM alembic_version) <> NEW.activation_revision
    THEN RAISE(ABORT, 'pta_receipt_revision_mismatch') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM persistent_team_control c WHERE c.id = NEW.control_id
      AND c.activated_at IS NULL AND c.rules_from_week_start IS NULL
      AND c.staged_plan_sha256 = NEW.plan_sha256 AND c.staged_for = NEW.effective_at
      AND json_extract(c.staged_plan, '$.content.authority_sha256') = NEW.authority_sha256)
    THEN RAISE(ABORT, 'pta_receipt_staged_authority_required') END;
END"""
SQLITE_UPDATE = """CREATE TRIGGER pta_receipt_update BEFORE UPDATE ON persistent_team_receipt_issuances
BEGIN SELECT RAISE(ABORT, 'pta_receipt_immutable'); END"""
SQLITE_DELETE = """CREATE TRIGGER pta_receipt_delete BEFORE DELETE ON persistent_team_receipt_issuances
BEGIN SELECT RAISE(ABORT, 'pta_receipt_immutable'); END"""

PG_INSERT_BODY = """
BEGIN
  PERFORM id FROM persistent_team_control WHERE id = NEW.control_id FOR UPDATE;
  IF EXISTS (SELECT 1 FROM persistent_team_receipt_issuances) THEN
    RAISE EXCEPTION 'pta_receipt_already_issued' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM alembic_version) <> 1
    OR (SELECT version_num FROM alembic_version) <> NEW.activation_revision THEN
    RAISE EXCEPTION 'pta_receipt_revision_mismatch' USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM persistent_team_control c WHERE c.id = NEW.control_id
      AND c.activated_at IS NULL AND c.rules_from_week_start IS NULL
      AND c.staged_plan_sha256 = NEW.plan_sha256 AND c.staged_for = NEW.effective_at
      AND c.staged_plan #>> '{content,authority_sha256}' = NEW.authority_sha256) THEN
    RAISE EXCEPTION 'pta_receipt_staged_authority_required' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;
"""
PG_IMMUTABLE_BODY = """
BEGIN
  RAISE EXCEPTION 'pta_receipt_immutable' USING ERRCODE='23514';
END;
"""
PG_TRIGGERS = {
    "pta_receipt_insert": ("INSERT", "ROW", "enforce_pta_receipt_insert", PG_INSERT_BODY, 7),
    "pta_receipt_change": ("UPDATE OR DELETE", "ROW", "enforce_pta_receipt_immutable", PG_IMMUTABLE_BODY, 27),
    "pta_receipt_truncate": ("TRUNCATE", "STATEMENT", "enforce_pta_receipt_immutable", PG_IMMUTABLE_BODY, 34),
}


def install(connection):
    if connection.dialect.name == "sqlite":
        for sql in (SQLITE_INSERT, SQLITE_UPDATE, SQLITE_DELETE):
            connection.exec_driver_sql(sql)
    elif connection.dialect.name == "postgresql":
        schema = connection.scalar(text("SELECT current_schema()"))
        quoted = connection.dialect.identifier_preparer.quote_schema(schema)
        for function, body in (("enforce_pta_receipt_insert", PG_INSERT_BODY),
                               ("enforce_pta_receipt_immutable", PG_IMMUTABLE_BODY)):
            connection.exec_driver_sql(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql "
                f"SET search_path = pg_catalog, {quoted}, pg_temp AS $$" + body + "$$")
        for name, (action, level, function, _, _) in PG_TRIGGERS.items():
            connection.exec_driver_sql(f"CREATE TRIGGER {name} BEFORE {action} ON {NAME} "
                f"FOR EACH {level} EXECUTE FUNCTION {function}()")
            connection.exec_driver_sql(f"ALTER TABLE {NAME} ENABLE ALWAYS TRIGGER {name}")
    else:
        raise RuntimeError("pta_receipt_unsupported_database")


def uninstall(connection):
    if connection.dialect.name == "postgresql":
        for name in PG_TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER {name} ON {NAME}")
        for function in ("enforce_pta_receipt_insert", "enforce_pta_receipt_immutable"):
            connection.exec_driver_sql(f"DROP FUNCTION {function}()")
    else:
        for name in ("pta_receipt_insert", "pta_receipt_update", "pta_receipt_delete"):
            connection.exec_driver_sql("DROP TRIGGER " + name)


def validate_guards(connection, error):
    def refuse():
        raise error("pta_receipt_schema_missing_or_changed: guards")
    if connection.dialect.name == "sqlite":
        found = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=:name"), {"name": NAME}).all())
        expected = {"pta_receipt_insert": SQLITE_INSERT, "pta_receipt_update": SQLITE_UPDATE,
                    "pta_receipt_delete": SQLITE_DELETE}
        if set(found) != set(expected) or any(found[n].strip() != s.strip() for n, s in expected.items()):
            refuse()
    else:
        schema = connection.scalar(text("SELECT current_schema()"))
        quoted = connection.dialect.identifier_preparer.quote_schema(schema)
        if connection.scalar(text("""
          SELECT EXISTS (
            SELECT 1 FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint
            WHERE c.conrelid='persistent_team_receipt_issuances'::regclass
              AND c.contype='f' AND t.tgenabled NOT IN ('O', 'A'))
        """)):
            refuse()
        rows = connection.execute(text("""
          SELECT t.tgname, t.tgenabled, t.tgtype, t.tgqual, t.tgattr::text AS attributes,
                 t.tgnargs, p.proname, p.prosrc, p.prosecdef, p.provolatile, p.proconfig,
                 n.nspname, l.lanname
          FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
          JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
          WHERE t.tgrelid='persistent_team_receipt_issuances'::regclass AND NOT t.tgisinternal
        """)).mappings().all()
        if {r["tgname"] for r in rows} != set(PG_TRIGGERS) or len(rows) != len(PG_TRIGGERS):
            refuse()
        for r in rows:
            _, _, function, body, kind = PG_TRIGGERS[r["tgname"]]
            if (r["tgenabled"] != "A" or r["tgtype"] != kind or r["tgqual"] is not None
                    or r["attributes"] or r["tgnargs"] != 0 or r["proname"] != function
                    or r["prosrc"].strip() != body.strip() or r["prosecdef"]
                    or r["provolatile"] != "v" or r["nspname"] != schema or r["lanname"] != "plpgsql"
                    or r["proconfig"] != [f"search_path=pg_catalog, {quoted}, pg_temp"]):
                refuse()
