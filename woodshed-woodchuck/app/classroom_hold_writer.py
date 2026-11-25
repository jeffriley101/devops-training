"""Bind one authorized Classroom transition to its ordinary writer transaction."""
from contextlib import contextmanager
import json
import os

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .classroom import ClassroomDenied
from .classroom_hold_guard import CONSENT_LOCK, SCOPE, validate


def lock_scoped_consent(session, scope, profile_id, consent_id):
    """Use the read-only C37 locker only for its explicitly granted roster writer."""
    if scope is None or session.get_bind().dialect.name != "postgresql":
        return False
    granted = session.scalar(text("""SELECT EXISTS (SELECT FROM pg_catalog.pg_proc p,
        LATERAL pg_catalog.aclexplode(p.proacl) a WHERE p.oid=pg_catalog.to_regprocedure(:fn)
        AND a.grantee=session_user::pg_catalog.regrole AND a.privilege_type='EXECUTE' AND NOT a.is_grantable
        AND p.proowner<>a.grantee)"""), {"fn": CONSENT_LOCK})
    if not granted:
        return False
    program, room, member = scope
    if member is None:
        # Guardian context already holds the Program lock, and locks its anchor
        # after consent and profile. Resolve its unique ID without changing that order.
        member = session.scalar(text("""SELECT id FROM public.classroom_student_memberships
            WHERE class_id=:c AND profile_id=:s"""), {"c": room, "s": profile_id})
    if not session.scalar(text("SELECT public.c37_lock_hold_consent(:p,:c,:m,:s,:consent)"),
            {"p": program, "c": room, "m": member, "s": profile_id, "consent": consent_id}):
        raise ClassroomDenied("Current account and parent authorization are required.")
    return True


def guarded(session):
    if session.get_bind().dialect.name != "postgresql":
        return False
    connection = session.connection()
    installed = connection.scalar(text("SELECT pg_catalog.to_regprocedure(:name) IS NOT NULL"), {"name": SCOPE})
    versioned = connection.scalar(text("SELECT pg_catalog.to_regclass('public.alembic_version') IS NOT NULL"))
    c37 = versioned and connection.scalar(text(
        "SELECT EXISTS (SELECT FROM public.alembic_version WHERE version_num IN ('c37hold001','c38disp001'))"))
    if not (installed or c37):
        return False
    if not (installed and c37):
        raise ClassroomDenied("C37 hold guard unavailable.")
    connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, public, pg_temp")
    validate(connection, ClassroomDenied)
    return True


@contextmanager
def witness(session, action, program, room, hold, *, erase=False):
    """The caller has authenticated the transition and holds the Program lock.

    The second login only holds read-only transaction advisory locks. No second
    transaction writes business data. Closing it withdraws the exact witness.
    """
    if action not in {"issue", "release", "erase"} or erase != (action == "erase"):
        raise ClassroomDenied("Invalid hold operation.")
    columns = ("membership_id","state","reason","held_by_verifier_id","held_at","released_at","released_by_verifier_id")
    values = {key:getattr(hold,key) for key in columns}
    if erase:
        values["held_by_verifier_id"] = None
    for key in ("held_at","released_at"):
        if values[key] is not None:
            values[key] = values[key].isoformat()
    keys = session.connection().scalar(text("""SELECT public.c37_hold_scope(CAST(:action AS text),
        CAST(:program AS integer),CAST(:room AS integer),
        (SELECT h FROM public.classroom_membership_holds h WHERE h.membership_id=:member),
        pg_catalog.jsonb_populate_record(NULL::public.classroom_membership_holds, CAST(:row AS jsonb)))"""),
        {"action":action,"program":program,"room":room,"member":hold.membership_id,"row":json.dumps(values)})
    role = "c37_disposition_witness" if erase else "c37_management_witness"
    with witness_locks(session, role, keys):
        yield


@contextmanager
def witness_locks(session, role, keys):
    """Keep the independent, transaction-only witness open for one write."""
    import psycopg

    target = session.info.get("disposition_target")
    if target is not None:
        from .school_disposition_connection import WITNESS
        if role != WITNESS:
            raise ClassroomDenied("Invalid disposition witness.")
        connect = lambda: target.connect(WITNESS)
    else:
        password = os.environ.get(role.upper()+"_PASSWORD")
        if not password:
            raise ClassroomDenied("Hold operation witness unavailable.")
        url = session.get_bind().url
        params = {k:url.query[k] for k in ("sslmode","sslrootcert","sslcert","sslkey") if k in url.query}
        params.update(dbname=url.database, host=url.host or url.query.get("host"), port=url.port or url.query.get("port"),
                      user=role, password=password, connect_timeout=3, application_name="woodshed-c37-witness")
        connect = lambda: psycopg.connect(**params)
    try:
        with connect() as observer:
            observer.execute("SET TRANSACTION READ ONLY")
            if observer.execute("SELECT session_user=current_user AND session_user=%s", (role,)).fetchone() != (True,):
                raise ClassroomDenied("Hold operation witness unavailable.")
            for a, b in ((keys[0], keys[1]), (keys[2], keys[3])):
                if not observer.execute("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(%s::int,%s::int)",
                                        (a, b)).fetchone()[0]:
                    raise ClassroomDenied("Hold operation witness busy.")
            yield
    except psycopg.Error:
        raise ClassroomDenied("Hold operation witness unavailable.") from None
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "42501" and str(error.orig).startswith("c37_"):
            raise ClassroomDenied("Authenticated hold transition refused.") from None
        raise


def management_guarded(session):
    if not guarded(session):
        return False
    identity = session.connection().execute(text("SELECT session_user,current_user")).one()
    from .classroom_writer_sessions import HOLD, qualified
    if tuple(identity) != (HOLD, HOLD) or not qualified(session, HOLD):
        raise ClassroomDenied("Hold writer privileges unqualified.")
    return True
