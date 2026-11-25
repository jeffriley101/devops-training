"""Server-selected Classroom connections. Each restricted request owns one physical session."""
from contextlib import contextmanager
import os

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from . import classroom_hold_guard as guard
from .classroom_role_membership import UNEXPECTED_MEMBERSHIP

HOLD = "c37_hold_writer"
AUTHORITY = "c37_program_authority"
HOLD_ACTIONS = frozenset({"remove", "suspend", "reinstate"})
AUTHORITY_ACTIONS = frozenset({"grant-role", "revoke-role", "propose-transfer", "cancel-transfer",
                              "accept-transfer", "assign-teacher", "end-teaching", "depart-teaching",
                              "teach", "accept-program-invitation"})

# ORM INSERT includes nullable fields. UPDATE authority is limited separately.
ENDS = "ended_at ended_reason ended_by_verifier_id"
AUTHORITY_COLUMNS = {
    "classroom_programs": ("organization_id owner_verifier_id owner_version created_at", "owner_verifier_id owner_version"),
    "classroom_role_grants": ("program_id verifier_id role starts_at granted_by_verifier_id " + ENDS, ENDS),
    "classroom_teaching_assignments": ("program_id class_id verifier_id starts_at granted_by_verifier_id " + ENDS, ENDS),
    "classroom_ownership_transfers": ("program_id initiator_verifier_id recipient_verifier_id owner_version status created_at resolved_at", "status resolved_at"),
    "classroom_audit_events": ("program_id class_id actor_verifier_id target_verifier_id action role_grant_id assignment_id transfer_id old_owner_id new_owner_id occurred_at", ""),
    "trusted_verifiers": ("email display_name pin_hash created_at updated_at", "display_name"),
    "organizations": ("", "name"),
    "classroom_classes": ("", "display_name"),
    "classroom_class_states": ("", "updated_at"),
    "woodchuck_profiles": ("", "display_name"),
    "classroom_student_memberships": ("", "created_at"),
}
HOLD_COLUMNS = {
    "trusted_verifiers": ("", "display_name"), "classroom_classes": ("", "display_name"),
    "classroom_programs": ("", "created_at"), "classroom_class_states": ("", "updated_at"),
    "classroom_student_memberships": ("", "created_at"), "woodchuck_profiles": ("", "display_name"),
    "classroom_membership_holds": ("membership_id state reason held_by_verifier_id held_at released_at released_by_verifier_id",
                                   "state reason held_by_verifier_id held_at released_at released_by_verifier_id"),
    "classroom_membership_periods": ("membership_id state starts_at ended_at ended_reason", "ended_at ended_reason"),
    "classroom_s2_audit_events": ("program_id class_id profile_id membership_id actor_verifier_id actor_profile_id actor_admin action reason entitlement_id code_id occurred_at", ""),
}


def qualified(db, role):
    if role not in (HOLD, AUTHORITY):
        return False
    trusted = db.scalar(text(f"""SELECT r.rolcanlogin AND NOT (r.rolinherit OR r.rolsuper OR r.rolcreaterole
        OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls
        OR {UNEXPECTED_MEMBERSHIP}
        OR has_database_privilege(r.oid,current_database(),'CREATE')
        OR has_parameter_privilege(r.oid,'session_replication_role','SET')
        OR EXISTS (SELECT FROM pg_namespace n WHERE n.nspname !~ '^pg_temp_' AND has_schema_privilege(r.oid,n.oid,'CREATE'))
        OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
          WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND (c.relowner=r.oid
            OR (c.relkind IN ('r','p','v','m','f') AND has_table_privilege(r.oid,c.oid,'DELETE,TRUNCATE,TRIGGER,REFERENCES'))
            OR (c.relkind='S' AND has_sequence_privilege(r.oid,c.oid,'UPDATE'))))
        OR EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
          WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND (p.proowner=r.oid OR
            (p.prosecdef AND p.oid IS DISTINCT FROM to_regprocedure(:locker) AND has_function_privilege(r.oid,p.oid,'EXECUTE')))))
        FROM pg_roles r WHERE rolname=:role"""), {"role": role, "locker": guard.CONSENT_LOCK})
    if not trusted:
        return False
    allowed = AUTHORITY_COLUMNS if role == AUTHORITY else HOLD_COLUMNS
    rows = db.execute(text("""SELECT n.nspname,c.relname,a.attname,p.v,
          has_column_privilege(r.oid,c.oid,a.attnum,p.v || ' WITH GRANT OPTION')
        FROM pg_roles r CROSS JOIN pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN (VALUES ('INSERT'),('UPDATE')) p(v)
        WHERE r.rolname=:role AND n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
          AND c.relkind IN ('r','p','v','f') AND a.attnum>0 AND NOT a.attisdropped
          AND has_column_privilege(r.oid,c.oid,a.attnum,p.v)"""), {"role":role})
    for schema, table, column, privilege, grantable in rows:
        permitted = allowed.get(table, ("", ""))[0 if privilege == "INSERT" else 1]
        if schema != "public" or grantable or column not in permitted.split():
            return False
    return role == AUTHORITY or guard.ordinary_role_safe(db, role)


def separated_runtime(db, role):
    """A shared account may run normal account services, but cannot write either protected boundary."""
    return db.scalar(text(f"""SELECT NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls
        OR r.rolreplication OR {UNEXPECTED_MEMBERSHIP}
        OR has_database_privilege(r.oid,current_database(),'CREATE')
        OR has_schema_privilege(r.oid,'public','CREATE')
        OR has_parameter_privilege(r.oid,'session_replication_role','SET')
        OR has_function_privilege(r.oid,:guard,'EXECUTE')
        OR EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND
              (p.proowner=r.oid OR (p.prosecdef AND has_function_privilege(r.oid,p.oid,'EXECUTE'))))
        OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN ('classroom_membership_holds','classroom_role_grants',
                'classroom_programs','classroom_teaching_assignments','classroom_ownership_transfers')
            AND (r.oid=c.relowner OR has_table_privilege(r.oid,c.oid,'DELETE,TRUNCATE,TRIGGER')
                OR has_any_column_privilege(r.oid,c.oid,'INSERT')
                OR EXISTS (SELECT FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (c.relname,a.attname) <> ('classroom_programs','created_at')
                    AND has_column_privilege(r.oid,c.oid,a.attnum,'UPDATE')))))
        FROM pg_roles r WHERE r.rolname=:role"""), {"role":role,"guard":guard.GUARD}) is True


@contextmanager
def operation_session(action, shared):
    role = HOLD if action in HOLD_ACTIONS else AUTHORITY if action in AUTHORITY_ACTIONS else None
    if role is None:
        with shared() as session:
            yield session
        return
    target = shared.kw["bind"].url
    password = os.environ.get(role.upper() + "_PASSWORD")
    # Build from trusted server coordinates. Never inherit the normal login, libpq
    # service selection, query credentials, startup options or a client-selected DSN.
    if (target.get_backend_name() != "postgresql" or not password or not target.database
            or not (target.host or target.query.get("host"))
            or set(target.query) - {"host", "port", "sslmode", "sslrootcert", "sslcert", "sslkey"}):
        raise HTTPException(503, "Restricted Classroom connection unavailable.")
    url = URL.create("postgresql+psycopg", username=role, password=password, database=target.database,
                     host=target.host, port=target.port, query=target.query)
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout":3})
    try:
        with sessionmaker(engine, autoflush=False, expire_on_commit=False)() as session:
            try:
                session.execute(text("SET LOCAL search_path TO pg_catalog, public, pg_temp"))
                identity = session.execute(text("SELECT session_user,current_user,current_database()")).one()
                if tuple(identity) != (role, role, target.database) or not qualified(session, role):
                    raise HTTPException(503, "Restricted Classroom connection unavailable.")
                from . import classroom_residual
                revisions = session.scalars(text("SELECT version_num FROM public.alembic_version")).all()
                if revisions not in ([classroom_residual.PREVIOUS], [classroom_residual.REVISION]):
                    raise HTTPException(503, "Restricted Classroom connection unavailable.")
                if revisions == [classroom_residual.REVISION]:
                    classroom_residual.validate(session.connection(), lambda _: HTTPException(503, "Restricted Classroom guard unavailable."))
                guard.validate(session.connection(), lambda _: HTTPException(503, "Restricted Classroom guard unavailable."))
            except SQLAlchemyError:
                raise HTTPException(503, "Restricted Classroom connection unavailable.") from None
            try:
                yield session
            except SQLAlchemyError:
                session.rollback()
                # PostgreSQL detail strings can contain row values even when
                # SQLAlchemy hides bound parameters. Return only a stable status.
                raise HTTPException(503, "Restricted Classroom operation unavailable.") from None
    finally:
        engine.dispose()
