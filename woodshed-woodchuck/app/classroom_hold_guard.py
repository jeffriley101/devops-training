"""PostgreSQL C37 hold transition contract. Both functions run as their caller."""
from sqlalchemy import text

from .classroom_role_membership import UNEXPECTED_MEMBERSHIP

TABLE = "public.classroom_membership_holds"
SCOPE = "public.c37_hold_scope(text,integer,integer,public.classroom_membership_holds,public.classroom_membership_holds)"
GUARD = "public.c37_guard_hold()"
CONSENT_LOCK = "public.c37_lock_hold_consent(integer,integer,integer,integer,integer)"

# PostgreSQL's FOR UPDATE otherwise needs an UPDATE privilege on an actual
# consent column. Return only whether this exact currently linked row was locked.
_C37_CONSENT_MEMBERSHIP = """EXISTS
        (SELECT FROM pg_auth_members m WHERE r.oid IN (m.member,m.roleid))"""
_CONSENT_TEMPLATE = """
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles r JOIN pg_proc p ON p.oid=
      to_regprocedure('public.c37_lock_hold_consent(integer,integer,integer,integer,integer)')
      CROSS JOIN LATERAL aclexplode(p.proacl) a
      WHERE r.rolname=session_user AND a.grantee=r.oid AND a.privilege_type='EXECUTE'
      AND NOT a.is_grantable AND NOT {unsafe_membership}) THEN
    RAISE EXCEPTION 'c37_consent_lock_denied' USING ERRCODE='42501';
  END IF;
  PERFORM e.id FROM public.child_consent_evidence e
    JOIN public.account_privacy_rules pr ON pr.consent_id=e.id AND pr.profile_id=e.profile_id
    JOIN public.classroom_student_memberships m ON m.profile_id=pr.profile_id
    JOIN public.classroom_classes c ON c.id=m.class_id
    WHERE c.program_id=$1 AND c.id=$2 AND m.id=$3 AND pr.profile_id=$4 AND e.id=$5
    FOR UPDATE OF e;
  RETURN FOUND;
END
"""

CONSENT_BODY = _CONSENT_TEMPLATE.format(unsafe_membership=_C37_CONSENT_MEMBERSHIP)
C38_CONSENT_BODY = _CONSENT_TEMPLATE.format(unsafe_membership=UNEXPECTED_MEMBERSHIP)

# The scope is public. Authority comes from the separate authenticated backend
# holding BOTH 64-bit advisory locks while the exact writer statement executes.
SCOPE_BODY = """
WITH material AS (
  SELECT encode(sha256(convert_to(jsonb_build_array('woodshed-c37-v1',current_database(),
    pg_backend_pid(),pg_current_xact_id()::text,session_user, $1,$2,$3,to_jsonb($4),to_jsonb($5))::text,
    'UTF8')),'hex') AS bytes
)
SELECT ARRAY(SELECT ('x'||substr(bytes,offset_index,8))::bit(32)::int
             FROM material, generate_series(1,25,8) AS offset_index ORDER BY offset_index)
"""
_C37_WITNESS_MEMBERSHIP = "EXISTS (SELECT FROM pg_auth_members am WHERE r.oid IN (am.member,am.roleid))"
_GUARD_TEMPLATE = """
DECLARE
  action_name text;
  witness_name text;
  room integer;
  program integer;
  keys integer[];
  original public.classroom_membership_holds;
BEGIN
  IF TG_OP IN ('DELETE','TRUNCATE') OR current_user <> session_user THEN
    RAISE EXCEPTION 'c37_hold_scope_denied' USING ERRCODE='42501';
  END IF;
  IF TG_OP='UPDATE' THEN
    original := OLD;
    IF NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
    IF NEW.membership_id <> OLD.membership_id THEN
      RAISE EXCEPTION 'c37_hold_binding_denied' USING ERRCODE='42501';
    END IF;
  END IF;
  IF TG_OP='UPDATE' AND OLD.held_by_verifier_id IS NOT NULL AND NEW.held_by_verifier_id IS NULL
     AND OLD.released_at IS NULL AND
     (to_jsonb(OLD)-'held_by_verifier_id')=(to_jsonb(NEW)-'held_by_verifier_id') THEN
    action_name := 'erase';
    witness_name := 'c37_disposition_witness';
    IF session_user <> 'disposition_custodian' THEN
      RAISE EXCEPTION 'c37_hold_disposition_denied' USING ERRCODE='42501';
    END IF;
  ELSIF TG_OP='UPDATE' AND OLD.released_at IS NULL AND NEW.released_at IS NOT NULL
      AND NEW.released_by_verifier_id IS NOT NULL AND
      (to_jsonb(OLD)-'released_at'-'released_by_verifier_id') =
      (to_jsonb(NEW)-'released_at'-'released_by_verifier_id') THEN
    action_name := 'release';
    witness_name := 'c37_management_witness';
  ELSIF NEW.held_by_verifier_id IS NOT NULL AND NEW.released_at IS NULL
      AND NEW.released_by_verifier_id IS NULL THEN
    action_name := 'issue';
    witness_name := 'c37_management_witness';
  ELSE
    RAISE EXCEPTION 'c37_hold_transition_denied' USING ERRCODE='42501';
  END IF;
  IF action_name IN ('issue','release') AND session_user <> 'c37_hold_writer' THEN
    RAISE EXCEPTION 'c37_hold_writer_required' USING ERRCODE='42501';
  END IF;
  SELECT c.program_id,c.id INTO STRICT program,room
    FROM public.classroom_student_memberships m JOIN public.classroom_classes c ON c.id=m.class_id
    WHERE m.id=NEW.membership_id;
  keys := public.c37_hold_scope(action_name,program,room,original,NEW);
  PERFORM pg_stat_clear_snapshot();
  IF NOT EXISTS (
    SELECT FROM pg_stat_activity a
    JOIN pg_roles r ON r.oid=a.usesysid
    JOIN pg_locks x ON x.pid=a.pid AND x.database=a.datid
    JOIN pg_locks y ON y.pid=a.pid AND y.database=a.datid
    WHERE a.datname=current_database() AND a.pid<>pg_backend_pid()
      AND r.rolname=witness_name AND r.rolcanlogin AND NOT (r.rolsuper OR r.rolcreaterole
        OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
      AND NOT {unsafe_membership}
      AND x.locktype='advisory' AND y.locktype='advisory' AND x.granted AND y.granted
      AND x.mode='ShareLock' AND y.mode='ShareLock' AND x.objsubid=2 AND y.objsubid=2
      AND x.classid=(keys[1]::bigint & 4294967295)::oid AND x.objid=(keys[2]::bigint & 4294967295)::oid
      AND y.classid=(keys[3]::bigint & 4294967295)::oid AND y.objid=(keys[4]::bigint & 4294967295)::oid
  ) THEN RAISE EXCEPTION 'c37_authenticated_hold_operation_required' USING ERRCODE='42501'; END IF;
  RETURN NEW;
END
"""
GUARD_BODY = _GUARD_TEMPLATE.format(unsafe_membership=_C37_WITNESS_MEMBERSHIP)

TRIGGERS = {"c37_hold_change": ("INSERT OR UPDATE OR DELETE", "ROW", 31),
            "c37_hold_truncate": ("TRUNCATE", "STATEMENT", 34)}


def install(connection):
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(f"CREATE FUNCTION {SCOPE} RETURNS integer[] LANGUAGE sql VOLATILE "
        "SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET TimeZone='UTC' AS $$"+SCOPE_BODY+"$$")
    connection.exec_driver_sql(f"CREATE FUNCTION {GUARD} RETURNS trigger LANGUAGE plpgsql VOLATILE "
        "SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET TimeZone='UTC' AS $$"+GUARD_BODY+"$$")
    connection.exec_driver_sql(f"REVOKE ALL ON FUNCTION {GUARD} FROM PUBLIC")
    connection.exec_driver_sql(f"CREATE FUNCTION {CONSENT_LOCK} RETURNS boolean LANGUAGE plpgsql VOLATILE "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET TimeZone='UTC' AS $$"+CONSENT_BODY+"$$")
    connection.exec_driver_sql(f"REVOKE ALL ON FUNCTION {CONSENT_LOCK} FROM PUBLIC")
    for name,(actions,level,_) in TRIGGERS.items():
        connection.exec_driver_sql(f"CREATE TRIGGER {name} BEFORE {actions} ON {TABLE} "
                                  f"FOR EACH {level} EXECUTE FUNCTION {GUARD}")
        connection.exec_driver_sql(f"ALTER TABLE {TABLE} ENABLE ALWAYS TRIGGER {name}")


def uninstall(connection):
    if connection.dialect.name == "postgresql":
        for name in TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER {name} ON {TABLE}")
        connection.exec_driver_sql(f"DROP FUNCTION {GUARD}")
        connection.exec_driver_sql(f"DROP FUNCTION {SCOPE}")
        connection.exec_driver_sql(f"DROP FUNCTION {CONSENT_LOCK}")


def ordinary_role_safe(connection, name):
    """Reject ownership, alternate role identities and control over the guard or witnesses."""
    return connection.scalar(text(f"""SELECT NOT (
        r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls OR
        {UNEXPECTED_MEMBERSHIP} OR
        r.oid IN (SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database()) OR
        r.oid IN (SELECT relowner FROM pg_catalog.pg_class WHERE oid=to_regclass(:tab)) OR
        r.oid IN (SELECT proowner FROM pg_catalog.pg_proc WHERE oid IN
          (to_regprocedure(:scope),to_regprocedure(:guard),to_regprocedure(:consent))) OR
        has_schema_privilege(r.oid,'public','CREATE') OR
        has_table_privilege(r.oid,:tab,'TRIGGER') OR has_table_privilege(r.oid,:tab,'TRUNCATE') OR
        has_table_privilege(r.oid,:tab,'DELETE') OR has_function_privilege(r.oid,:guard,'EXECUTE') OR
        has_any_column_privilege(r.oid,:tab,'UPDATE WITH GRANT OPTION') OR
        has_any_column_privilege(r.oid,:tab,'INSERT WITH GRANT OPTION') OR
        EXISTS (SELECT FROM (VALUES
          ('public.trusted_verifiers','display_name'),('public.classroom_programs','created_at'),
          ('public.classroom_classes','display_name'),('public.classroom_student_memberships','created_at'),
          ('public.woodchuck_profiles','display_name'),('public.classroom_class_states','updated_at'),
          ('public.classroom_role_grants',''),('public.classroom_school_authorizations',''),
          ('public.classroom_teaching_assignments',''),('public.classroom_entitlements',''),
          ('public.account_privacy_rules',''),('public.child_consent_evidence',''),
          ('public.child_kws_verifications',''),('public.child_pending_consents',''),
          ('public.classroom_guardian_authorizations',''),('public.classroom_guardian_recipients','')
        ) AS auth(relation,lock_column) JOIN pg_catalog.pg_class t ON t.oid=to_regclass(auth.relation)
        WHERE r.oid=t.relowner OR has_table_privilege(r.oid,t.oid,'DELETE, TRUNCATE, TRIGGER')
          OR has_any_column_privilege(r.oid,t.oid,'INSERT')
          OR EXISTS (SELECT FROM pg_catalog.pg_attribute a WHERE a.attrelid=t.oid AND a.attnum>0
            AND NOT a.attisdropped AND a.attname<>auth.lock_column
            AND has_column_privilege(r.oid,t.oid,a.attnum,'UPDATE'))) OR
        has_parameter_privilege(r.oid,'session_replication_role','SET'))
        FROM pg_catalog.pg_roles r WHERE r.rolname=:role"""),
        {"role":name,"tab":TABLE,"scope":SCOPE,"guard":GUARD,"consent":CONSENT_LOCK}) is True


def retirement_guard_body():
    # Preserve the C37 body and its exact writer checks on every other path.
    first_guard = "  IF TG_OP IN ('DELETE','TRUNCATE')"
    released = """  IF TG_OP='DELETE' AND current_user=session_user AND session_user='disposition_custodian'
     AND OLD.released_at IS NOT NULL AND OLD.released_by_verifier_id IS NOT NULL THEN
    IF public.c38_require_evidence_witness(TG_TABLE_NAME, TG_OP, to_jsonb(OLD), NULL) THEN RETURN OLD; END IF;
  END IF;
"""
    body = _GUARD_TEMPLATE.format(unsafe_membership=UNEXPECTED_MEMBERSHIP)
    return body.replace(first_guard, released + first_guard)


def validate(connection, error):
    if connection.dialect.name != "postgresql":
        return
    rows = connection.execute(text("""SELECT t.tgname,t.tgtype,t.tgenabled,t.tgqual IS NULL,
        t.tgattr::text,t.tgnargs,t.tgfoid=to_regprocedure(:guard)
        FROM pg_catalog.pg_trigger t WHERE t.tgrelid=to_regclass(:tab) AND NOT t.tgisinternal"""),
        {"guard": GUARD, "tab": TABLE}).all()
    expected = {(name,kind,"A",True,"",0,True) for name,(_,_,kind) in TRIGGERS.items()}
    if set(rows) != expected:
        raise error("c37_hold_trigger_unqualified")
    c38 = connection.scalar(text("SELECT EXISTS (SELECT FROM public.alembic_version WHERE version_num='c38disp001')"))
    for signature, body, language in ((GUARD, retirement_guard_body() if c38 else GUARD_BODY, "plpgsql"), (SCOPE, SCOPE_BODY, "sql"),
                                     (CONSENT_LOCK, C38_CONSENT_BODY if c38 else CONSENT_BODY, "plpgsql")):
        row = connection.execute(text("""SELECT p.prosrc,p.prosecdef,p.provolatile,p.proconfig,l.lanname,
            p.proowner=(SELECT relowner FROM pg_catalog.pg_class WHERE oid=to_regclass(:table))
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE p.oid=to_regprocedure(:signature)"""), {"signature":signature,"table":TABLE}).one_or_none()
        if not row or not row[5] or (row[0].strip(),row[1],row[2],row[3],row[4]) != (
                body.strip(),signature == CONSENT_LOCK,"v",["search_path=pg_catalog, pg_temp","TimeZone=UTC"],language):
            raise error("c37_hold_function_unqualified")
    if connection.scalar(text("""SELECT EXISTS (SELECT FROM pg_catalog.pg_proc p,
        LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
        WHERE p.oid IN (to_regprocedure(:consent),to_regprocedure(:guard)) AND a.grantee=0
        AND a.privilege_type='EXECUTE')"""), {"consent":CONSENT_LOCK,"guard":GUARD}):
        raise error("c37_hold_function_public_execution")
