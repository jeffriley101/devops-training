"""Append-only saved versions, including protection against PostgreSQL TRUNCATE."""
from sqlalchemy import text
NAME = 'classroom_choice_versions'
SQLITE_UPDATE = "CREATE TRIGGER classroom_choice_update BEFORE UPDATE ON classroom_choice_versions BEGIN SELECT RAISE(ABORT, 'classroom_choice_immutable'); END"
SQLITE_DELETE = "CREATE TRIGGER classroom_choice_delete BEFORE DELETE ON classroom_choice_versions BEGIN SELECT RAISE(ABORT, 'classroom_choice_immutable'); END"
PG_IMMUTABLE_BODY = """
BEGIN
  RAISE EXCEPTION 'classroom_choice_immutable' USING ERRCODE='23514';
END;
"""
PG_TRIGGERS = {
    'classroom_choice_change': ('UPDATE OR DELETE', 'ROW', 'enforce_classroom_choice_immutable', PG_IMMUTABLE_BODY, 27),
    'classroom_choice_truncate': ('TRUNCATE', 'STATEMENT', 'enforce_classroom_choice_immutable', PG_IMMUTABLE_BODY, 34),
}


def install(connection):
    if connection.dialect.name == 'sqlite':
        for sql in (SQLITE_UPDATE, SQLITE_DELETE):
            connection.exec_driver_sql(sql)
    elif connection.dialect.name == 'postgresql':
        schema = connection.scalar(text('SELECT current_schema()'))
        quoted = connection.dialect.identifier_preparer.quote_schema(schema)
        connection.exec_driver_sql('CREATE FUNCTION enforce_classroom_choice_immutable() RETURNS trigger LANGUAGE plpgsql '
            f'SET search_path = pg_catalog, {quoted}, pg_temp AS $$' + PG_IMMUTABLE_BODY + '$$')
        for name, (action, level, function, _, _) in PG_TRIGGERS.items():
            connection.exec_driver_sql(f'CREATE TRIGGER {name} BEFORE {action} ON {NAME} '
                f'FOR EACH {level} EXECUTE FUNCTION {function}()')
            connection.exec_driver_sql(f'ALTER TABLE {NAME} ENABLE ALWAYS TRIGGER {name}')
    else:
        raise RuntimeError('classroom_choice_unsupported_database')


def validate_guards(connection, error):
    def refuse():
        raise error("classroom_choice_schema_missing_or_changed: guards")
    if connection.dialect.name == "sqlite":
        found = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=:name"), {"name": NAME}).all())
        expected = {"classroom_choice_update": SQLITE_UPDATE,
                    "classroom_choice_delete": SQLITE_DELETE}
        if set(found) != set(expected) or any(found[n].strip() != s.strip() for n, s in expected.items()):
            refuse()
    else:
        schema = connection.scalar(text("SELECT current_schema()"))
        quoted = connection.dialect.identifier_preparer.quote_schema(schema)
        if connection.scalar(text("""
          SELECT EXISTS (
            SELECT 1 FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint
            WHERE c.conrelid='classroom_choice_versions'::regclass
              AND c.contype='f' AND t.tgenabled NOT IN ('O', 'A'))
        """)):
            refuse()
        rows = connection.execute(text("""
          SELECT t.tgname, t.tgenabled, t.tgtype, t.tgqual, t.tgattr::text AS attributes,
                 t.tgnargs, p.proname, p.prosrc, p.prosecdef, p.provolatile, p.proconfig,
                 n.nspname, l.lanname
          FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
          JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
          WHERE t.tgrelid='classroom_choice_versions'::regclass AND NOT t.tgisinternal
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
