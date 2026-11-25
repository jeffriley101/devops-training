"""C38's catalog check for a restricted login (pg_roles alias r)."""

# Pin the administrator independently of the login being checked or the caller's
# connection settings. PostgreSQL reserves OID 10 for the bootstrap superuser.
ADMINISTRATOR = "woodshed_woodchuck_db_user"

EXPECTED_CREATOR_GRANT = f"""(
    m.roleid = r.oid AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option
    AND r.rolname IN ('disposition_ordinary','c37_hold_writer','c37_program_authority',
        'c37_management_witness','disposition_custodian','c37_disposition_witness',
        'disposition_verifier','ww_kws_retention','ww_contest_finalizer','ww_support_adult')
    AND m.grantor = 10 AND EXISTS (
        SELECT FROM pg_catalog.pg_roles bootstrap WHERE bootstrap.oid = m.grantor AND bootstrap.rolsuper)
    AND EXISTS (
        SELECT FROM pg_catalog.pg_roles administrator
        JOIN pg_catalog.pg_database d ON d.datdba = administrator.oid
        WHERE d.datname = pg_catalog.current_database() AND administrator.oid = m.member
          AND administrator.rolname = '{ADMINISTRATOR}' AND administrator.rolcanlogin
          AND administrator.rolcreaterole AND NOT administrator.rolsuper)
    AND NOT EXISTS (
        SELECT FROM pg_catalog.pg_auth_members upstream WHERE upstream.roleid = m.member)
)"""

UNEXPECTED_MEMBERSHIP = f"""EXISTS (
    SELECT FROM pg_catalog.pg_auth_members m
    WHERE r.oid IN (m.roleid, m.member) AND NOT {EXPECTED_CREATOR_GRANT})"""
