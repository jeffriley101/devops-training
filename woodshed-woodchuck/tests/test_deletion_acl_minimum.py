"""Prove each added guardian privilege is necessary for the candidate's actual SQL."""
from collections import Counter

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deletion_disposal import associated_disposal
from tests.test_associated_disposal_support import private_rows, qualify_student_disposal
from tests.test_parent_deletion_support import support_case


def _effective_permissions(connection):
    # Include effective table, column, inherited and PUBLIC rights. Diffing the two
    # snapshots avoids counting a column that was already readable via table SELECT.
    return set(connection.execute(text("""
        WITH relations AS (
            SELECT oid, relname FROM pg_catalog.pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p')
        )
        SELECT r.relname, priv.name, a.attname FROM relations r
        JOIN pg_catalog.pg_attribute a ON a.attrelid = r.oid
        CROSS JOIN (VALUES ('SELECT'), ('UPDATE')) AS priv(name)
        WHERE a.attnum > 0 AND NOT a.attisdropped
          AND pg_catalog.has_column_privilege(r.oid, a.attnum, priv.name)
        UNION ALL
        SELECT relname, 'DELETE', NULL FROM relations
        WHERE pg_catalog.has_table_privilege(oid, 'DELETE')
    """)).tuples())


def test_every_added_guardian_permission_is_required(play_pg, support_case):
    _, _, case, _, _, engine = support_case
    role = engine.url.username
    with play_pg.begin() as admin:
        private_rows(admin, case["profile_id"], "minimum")
    with engine.connect() as login:
        assert login.execute(text("SELECT session_user, current_user")).one() == (role, role)
        before = _effective_permissions(login)
    qualify_student_disposal(play_pg, engine)
    with engine.connect() as login:
        added = _effective_permissions(login) - before
    assert Counter(privilege for _, privilege, _ in added) == {"SELECT": 50, "UPDATE": 10, "DELETE": 16}

    def run_disposal():
        # Each real disposal runs in its own transaction against the same rows.
        # Closing the session rolls it back, including released savepoints.
        with Session(engine) as session:
            return set(associated_disposal(session, case["profile_id"]).values())

    assert run_disposal() == {"verified"}
    quote = engine.dialect.identifier_preparer.quote
    for table, privilege, column in sorted(added):
        grant = f"{privilege} ({quote(column)})" if column else privilege
        target = f"{grant} ON TABLE public.{quote(table)}"
        with play_pg.begin() as admin:
            admin.execute(text(f"REVOKE {target} FROM {quote(role)}"))
        assert "access_required" in run_disposal(), (table, privilege, column)
        with play_pg.begin() as admin:
            admin.execute(text(f"GRANT {target} TO {quote(role)}"))
    assert run_disposal() == {"verified"}
