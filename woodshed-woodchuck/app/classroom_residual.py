"""Structural Classroom state used by the PostgreSQL disposition revision."""
from contextlib import contextmanager

from sqlalchemy import bindparam, func, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB

from .classroom_models import ClassroomTeachingAssignment
from .classroom_role_membership import UNEXPECTED_MEMBERSHIP

REVISION = "c38disp001"
PREVIOUS = "c37hold001"


def enabled(session):
    if session.get_bind().dialect.name != "postgresql":
        return False
    present = session.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
    if not present:
        return False
    return session.scalars(text("SELECT version_num FROM public.alembic_version")).all() == [REVISION]


def retired_teachers(session, program_id):
    if not enabled(session):
        return 0
    return session.scalar(text("""
        SELECT retired_teaching_units FROM public.classroom_programs WHERE organization_id = :program
    """), {"program": program_id})


def historical_teacher_count(session, program_id):
    """Reporting upper bound: distinct retained accounts plus anonymous retired units."""
    retained = session.scalar(select(func.count(func.distinct(ClassroomTeachingAssignment.verifier_id))).where(
        ClassroomTeachingAssignment.program_id == program_id))
    return retained + retired_teachers(session, program_id)


def next_code_generation(session, program_id, class_id):
    return session.scalar(text("""
        SELECT entry_generation + 1 FROM public.classroom_class_states
        WHERE program_id = :program AND class_id = :class_id
    """), {"program": program_id, "class_id": class_id})


@contextmanager
def evidence_witness(session, table, criteria, changes=None):
    """Witness the exact PostgreSQL row transition from the signed case plan."""
    from .classroom_hold_writer import witness_locks
    from .support_case_files import SupportDenied

    before = func.to_jsonb(table.table_valued())
    op = "UPDATE" if changes is not None else "DELETE"
    after = before.op("||")(literal(changes, type_=JSONB)) if changes is not None else None
    keys = session.scalar(select(func.public.c38_evidence_scope(table.name, op, before, after))
                          .select_from(table).where(*criteria))
    if keys is None:
        raise SupportDenied("disposition_witness_record_changed")
    with witness_locks(session, "c37_disposition_witness", keys):
        yield


SCOPE_SQL = """
CREATE FUNCTION public.c38_evidence_scope(text, text, jsonb, jsonb) RETURNS integer[]
LANGUAGE sql VOLATILE SET search_path = pg_catalog, public, pg_temp AS $$
WITH material AS (
    SELECT encode(sha256(convert_to(jsonb_build_array('woodshed-c38-v1', current_database(),
           pg_backend_pid(), pg_current_xact_id()::text, session_user, $1, $2, $3, $4)::text, 'UTF8')), 'hex') AS bytes
)
SELECT ARRAY(SELECT ('x' || substr(bytes, n, 8))::bit(32)::int
             FROM material, generate_series(1,25,8) AS n ORDER BY n)
$$
"""

WITNESS_SQL = f"""
CREATE FUNCTION public.c38_require_evidence_witness(text, text, jsonb, jsonb) RETURNS boolean
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    keys integer[];
BEGIN
    IF session_user <> 'disposition_custodian' THEN RETURN false; END IF;
    keys := public.c38_evidence_scope($1, $2, $3, $4);
    PERFORM pg_stat_clear_snapshot();
    RETURN EXISTS (
        SELECT FROM pg_stat_activity a JOIN pg_roles r ON r.oid = a.usesysid
        JOIN pg_locks x ON x.pid = a.pid AND x.database = a.datid
        JOIN pg_locks y ON y.pid = a.pid AND y.database = a.datid
        WHERE a.datname = current_database() AND a.pid <> pg_backend_pid()
          AND r.rolname = 'c37_disposition_witness' AND r.rolcanlogin
          AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
          AND NOT {UNEXPECTED_MEMBERSHIP}
          AND x.locktype = 'advisory' AND y.locktype = 'advisory' AND x.granted AND y.granted
          AND x.mode = 'ShareLock' AND y.mode = 'ShareLock' AND x.objsubid = 2 AND y.objsubid = 2
          AND x.classid = (keys[1]::bigint & 4294967295)::oid AND x.objid = (keys[2]::bigint & 4294967295)::oid
          AND y.classid = (keys[3]::bigint & 4294967295)::oid AND y.objid = (keys[4]::bigint & 4294967295)::oid
    );
END;
$$
"""

# Both witnesses bind the backend, login, transaction and complete before/after
# records. Their independent logins have disjoint responsibilities.
LIFECYCLE_WITNESS_SQL = (WITNESS_SQL
    .replace("c38_require_evidence_witness", "c38_require_lifecycle_witness")
    .replace("IF session_user <> 'disposition_custodian' THEN RETURN false; END IF;",
             "IF current_user <> session_user OR session_user = 'disposition_custodian' THEN RETURN false; END IF;")
    .replace("r.rolname = 'c37_disposition_witness'", "r.rolname = 'c37_management_witness'"))


def flush_lifecycle(session, state):
    """Called under the Program lock, following current director authorization."""
    if not enabled(session):
        return
    import json
    from .classroom_hold_writer import witness_locks
    row = {col.name: getattr(state, col.name) for col in state.__table__.columns}
    for name in ("created_at", "updated_at"):
        row[name] = row[name].isoformat()
    with session.no_autoflush:
        keys = session.scalar(text("""
            WITH original AS (
                SELECT to_jsonb(s) AS data FROM public.classroom_class_states s WHERE s.class_id=:class_id
            ), transition AS (
                SELECT (SELECT data FROM original) AS prior, CAST(:value AS jsonb) AS change
            )
            SELECT public.c38_evidence_scope('classroom_class_states',
                CASE WHEN prior IS NULL THEN 'INSERT' ELSE 'UPDATE' END, prior,
                to_jsonb(jsonb_populate_record(NULL::public.classroom_class_states,
                    coalesce(prior, jsonb_build_object('entry_generation',0)) || change)))
            FROM transition
        """), {"class_id": state.class_id, "value": json.dumps(row)})
    with witness_locks(session, "c37_management_witness", keys):
        session.flush([state])


DELETE_SQL = """
CREATE FUNCTION public.c38_guard_residual_delete() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF session_user = 'disposition_custodian' THEN
        IF public.c38_require_evidence_witness(TG_TABLE_NAME, TG_OP, to_jsonb(OLD), NULL) THEN
            RETURN OLD;
        END IF;
    END IF;
    RAISE EXCEPTION 'Classroom disposition requires its exact witnessed operation' USING ERRCODE = '42501';
END;
$$
"""


PRESERVE_SQL = """
CREATE FUNCTION public.c38_preserve_structural_history() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' AND session_user = 'disposition_custodian' THEN
        IF TG_TABLE_NAME = 'classroom_audit_events' THEN
            IF OLD.action NOT IN ('teacher_assigned','teacher_revoked','teacher_departed')
                OR (OLD.action = 'teacher_assigned' AND EXISTS (
                    SELECT FROM public.classroom_audit_events AS newer WHERE newer.program_id = OLD.program_id
                      AND newer.class_id = OLD.class_id AND newer.assignment_id = OLD.assignment_id
                      AND newer.id > OLD.id AND newer.occurred_at >= OLD.occurred_at
                      AND newer.action IN ('teacher_revoked','teacher_departed')
                ))
            THEN RETURN OLD; END IF;
        ELSIF TG_TABLE_NAME = 'classroom_s2_audit_events' THEN
            IF OLD.action IN ('enrollment_opened','enrollment_closed','code_issued','code_rotated','code_revoked')
                OR (OLD.action IN ('student_removed','student_suspended') AND EXISTS (
                    SELECT FROM public.classroom_s2_audit_events AS newer WHERE newer.program_id = OLD.program_id
                      AND newer.class_id = OLD.class_id AND newer.id > OLD.id
                      AND newer.occurred_at >= OLD.occurred_at AND newer.action = 'class_archived'
                ))
            THEN RETURN OLD; END IF;
        END IF;
    END IF;
    RAISE EXCEPTION 'Classroom structural history cannot be removed' USING ERRCODE = '42501';
END;
$$
"""


ACTOR_SQL = """
CREATE FUNCTION public.c38_guard_historical_attribution() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    clearing boolean;
BEGIN
    IF TG_TABLE_NAME = 'classroom_audit_events' THEN
        IF TG_OP = 'INSERT' AND NEW.actor_verifier_id IS NOT NULL THEN RETURN NEW; END IF;
        IF TG_OP = 'UPDATE' AND session_user = 'disposition_custodian'
            AND NEW.actor_verifier_id IS NULL
            AND (NEW.target_verifier_id IS NULL OR NEW.target_verifier_id IS NOT DISTINCT FROM OLD.target_verifier_id)
            AND (NEW.assignment_id IS NULL OR NEW.assignment_id IS NOT DISTINCT FROM OLD.assignment_id)
            AND (NEW.role_grant_id IS NULL OR NEW.role_grant_id IS NOT DISTINCT FROM OLD.role_grant_id)
            AND (NEW.old_owner_id IS NULL OR NEW.old_owner_id IS NOT DISTINCT FROM OLD.old_owner_id)
            AND (NEW.new_owner_id IS NULL OR NEW.new_owner_id IS NOT DISTINCT FROM OLD.new_owner_id)
            AND (to_jsonb(NEW) - ARRAY['actor_verifier_id','target_verifier_id','assignment_id','role_grant_id','old_owner_id','new_owner_id'])
              = (to_jsonb(OLD) - ARRAY['actor_verifier_id','target_verifier_id','assignment_id','role_grant_id','old_owner_id','new_owner_id'])
        THEN
            IF public.c38_require_evidence_witness(TG_TABLE_NAME, TG_OP, to_jsonb(OLD), to_jsonb(NEW)) THEN
                RETURN NEW;
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'classroom_s2_audit_events' THEN
        IF TG_OP = 'INSERT'
            AND num_nonnulls(NEW.actor_verifier_id, NEW.actor_profile_id, NEW.actor_admin) = 1
        THEN RETURN NEW; END IF;
        IF TG_OP = 'UPDATE' AND session_user = 'disposition_custodian'
            AND (num_nonnulls(NEW.actor_verifier_id, NEW.actor_profile_id, NEW.actor_admin) = 0
                OR (NEW.actor_verifier_id, NEW.actor_profile_id, NEW.actor_admin)
                    IS NOT DISTINCT FROM (OLD.actor_verifier_id, OLD.actor_profile_id, OLD.actor_admin))
            AND (NEW.entitlement_id IS NULL OR NEW.entitlement_id IS NOT DISTINCT FROM OLD.entitlement_id)
            AND (NEW.code_id IS NULL OR NEW.code_id IS NOT DISTINCT FROM OLD.code_id)
            AND ((NEW.profile_id IS NOT DISTINCT FROM OLD.profile_id
                  AND NEW.membership_id IS NOT DISTINCT FROM OLD.membership_id)
                OR (OLD.action IN ('student_joined','student_left','student_removed','student_suspended','student_reinstated')
                    AND (NEW.profile_id IS NULL OR NEW.profile_id IS NOT DISTINCT FROM OLD.profile_id)
                    AND (NEW.membership_id IS NULL OR NEW.membership_id IS NOT DISTINCT FROM OLD.membership_id)))
            AND (to_jsonb(NEW) - ARRAY['actor_verifier_id','actor_profile_id','actor_admin','entitlement_id','code_id','profile_id','membership_id'])
              = (to_jsonb(OLD) - ARRAY['actor_verifier_id','actor_profile_id','actor_admin','entitlement_id','code_id','profile_id','membership_id'])
        THEN
            IF public.c38_require_evidence_witness(TG_TABLE_NAME, TG_OP, to_jsonb(OLD), to_jsonb(NEW)) THEN
                RETURN NEW;
            END IF;
        END IF;
    ELSE
        IF TG_OP = 'INSERT' AND NEW.changed_by_verifier_id IS NOT NULL THEN
            IF public.c38_require_lifecycle_witness(TG_TABLE_NAME, TG_OP, NULL, to_jsonb(NEW)) THEN RETURN NEW; END IF;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            clearing := OLD.changed_by_verifier_id IS NOT NULL AND NEW.changed_by_verifier_id IS NULL;
            IF clearing AND session_user = 'disposition_custodian' AND OLD.state = 'archived'
                AND (to_jsonb(NEW) - 'changed_by_verifier_id') = (to_jsonb(OLD) - 'changed_by_verifier_id')
            THEN
                IF public.c38_require_evidence_witness(TG_TABLE_NAME, TG_OP, to_jsonb(OLD), to_jsonb(NEW)) THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF (to_jsonb(NEW) - 'entry_generation') = (to_jsonb(OLD) - 'entry_generation')
            THEN RETURN NEW; END IF;
            IF NOT clearing AND NEW.changed_by_verifier_id IS NOT NULL
                AND NEW.class_id = OLD.class_id AND NEW.program_id = OLD.program_id
                AND NEW.created_at = OLD.created_at AND NEW.entry_generation = OLD.entry_generation
                AND ((NEW.state <> OLD.state AND NOT NEW.enrollment_open)
                    OR (NEW.state = 'active' AND OLD.state = 'active'
                        AND NEW.enrollment_open <> OLD.enrollment_open))
                AND public.c38_require_lifecycle_witness(TG_TABLE_NAME, TG_OP, to_jsonb(OLD), to_jsonb(NEW))
            THEN RETURN NEW; END IF;
        END IF;
    END IF;
    RAISE EXCEPTION 'Classroom attribution requires the authorized operation' USING ERRCODE = '42501';
END;
$$
"""

CODE_SQL = """
CREATE FUNCTION public.c38_advance_entry_generation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NOT NEW.is_current THEN
        RAISE EXCEPTION 'New code requires current issuance authority' USING ERRCODE = '23514';
    END IF;
    UPDATE public.classroom_class_states
       SET entry_generation = NEW.generation
     WHERE class_id = NEW.class_id AND program_id = NEW.program_id
       AND state = 'active' AND entry_generation + 1 = NEW.generation;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Class entry generation must advance once' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""

COUNTER_SQL = """
CREATE FUNCTION public.c38_retire_teaching_identity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF session_user <> 'disposition_custodian' OR OLD.ended_at IS NULL OR OLD.ended_at > statement_timestamp() THEN
        RAISE EXCEPTION 'Assignment retirement requires the disposition custodian' USING ERRCODE = '42501';
    END IF;
    PERFORM 1 FROM public.classroom_programs WHERE organization_id = OLD.program_id FOR UPDATE;
    IF NOT EXISTS (
        SELECT 1 FROM public.classroom_teaching_assignments
        WHERE program_id = OLD.program_id AND verifier_id = OLD.verifier_id
    ) THEN
        UPDATE public.classroom_programs SET retired_teaching_units = retired_teaching_units + 1
        WHERE organization_id = OLD.program_id;
    END IF;
    RETURN OLD;
END;
$$
"""

STRUCTURAL_SCOPE_SQL = """
CREATE FUNCTION public.c38_guard_structural_scope() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    field_name text := TG_ARGV[0];
    next_value integer := (to_jsonb(NEW) ->> field_name)::integer;
BEGIN
    IF field_name = 'retired_teaching_units' THEN
        IF (TG_OP = 'INSERT' AND NEW.retired_allocation_bound <> 0) OR
           (TG_OP = 'UPDATE' AND NEW.retired_allocation_bound IS DISTINCT FROM OLD.retired_allocation_bound)
        THEN RAISE EXCEPTION 'Inactive allocation boundary cannot be changed'; END IF;
    END IF;
    IF TG_OP = 'INSERT' AND next_value = 0 THEN RETURN NEW; END IF;
    IF TG_OP = 'UPDATE' AND next_value = (to_jsonb(OLD) ->> field_name)::integer THEN RETURN NEW; END IF;
    IF TG_OP = 'UPDATE' AND pg_trigger_depth() = 2
        AND current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = TG_RELID))
        AND next_value = (to_jsonb(OLD) ->> field_name)::integer + 1
    THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Classroom structural state requires its scoped transition' USING ERRCODE = '42501';
END;
$$
"""

CODE_IDENTITY_SQL = """
CREATE FUNCTION public.c38_guard_entry_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' AND session_user = 'disposition_custodian' AND NOT OLD.is_current THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.is_current AND NOT NEW.is_current
        AND (to_jsonb(NEW) - ARRAY['is_current','revoked_at','revoked_by_verifier_id'])
        = (to_jsonb(OLD) - ARRAY['is_current','revoked_at','revoked_by_verifier_id'])
    THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Class entry identity and generation are immutable' USING ERRCODE = '42501';
END;
$$
"""

FORM_SQL = """
CREATE FUNCTION public.c38_retire_review_form() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF OLD.withdrawn_at IS NULL THEN RAISE EXCEPTION 'Current reporting consent cannot be retired'; END IF;
    UPDATE public.classroom_student_memberships SET reporting_form_revision = reporting_form_revision + 1
      WHERE id = OLD.membership_id AND class_id = OLD.class_id AND profile_id = OLD.profile_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Reporting membership scope changed'; END IF;
    RETURN OLD;
END;
$$
"""

FUNCTIONS = {
    "c38_retire_review_form": (FORM_SQL, True),
    "c38_evidence_scope": (SCOPE_SQL, False),
    "c38_require_evidence_witness": (WITNESS_SQL, False),
    "c38_require_lifecycle_witness": (LIFECYCLE_WITNESS_SQL, False),
    "c38_guard_residual_delete": (DELETE_SQL, False),
    "c38_preserve_structural_history": (PRESERVE_SQL, False),
    "c38_guard_historical_attribution": (ACTOR_SQL, False),
    "c38_advance_entry_generation": (CODE_SQL, True),
    "c38_retire_teaching_identity": (COUNTER_SQL, True),
    "c38_guard_structural_scope": (STRUCTURAL_SCOPE_SQL, False),
    "c38_guard_entry_identity": (CODE_IDENTITY_SQL, False),
}
WITNESS_FUNCTIONS = {"c38_evidence_scope", "c38_require_evidence_witness", "c38_require_lifecycle_witness"}
WITNESSED_DELETE_TABLES = {"classroom_progress_authorizations", "classroom_student_memberships","classroom_membership_periods", "classroom_reporting_periods",
                           "classroom_teaching_assignments", "classroom_entry_codes", "classroom_entitlements"}

# tgtype uses PostgreSQL's trigger bitmask (ROW, BEFORE, INSERT, DELETE, UPDATE).
TRIGGERS = {
    ("classroom_progress_authorizations", "c38_retire_form"): ("c38_retire_review_form", 9, "AFTER DELETE", None),
    ("classroom_student_memberships", "c38_form_revision"): ("c38_guard_structural_scope", 23, "BEFORE INSERT OR UPDATE", "reporting_form_revision"),
    ("classroom_audit_events", "c38_audit_attribution"): ("c38_guard_historical_attribution", 23, "BEFORE INSERT OR UPDATE", None),
    ("classroom_s2_audit_events", "c38_s2_attribution"): ("c38_guard_historical_attribution", 23, "BEFORE INSERT OR UPDATE", None),
    ("classroom_class_states", "c38_archive_attribution"): ("c38_guard_historical_attribution", 23, "BEFORE INSERT OR UPDATE", None),
    ("classroom_class_states", "c38_class_generation"): ("c38_guard_structural_scope", 23, "BEFORE INSERT OR UPDATE", "entry_generation"),
    ("classroom_programs", "c38_program_usage"): ("c38_guard_structural_scope", 23, "BEFORE INSERT OR UPDATE", "retired_teaching_units"),
    ("classroom_entry_codes", "c38_entry_identity"): ("c38_guard_entry_identity", 27, "BEFORE UPDATE OR DELETE", None),
    ("classroom_entry_codes", "c38_entry_generation"): ("c38_advance_entry_generation", 5, "AFTER INSERT", None),
    ("classroom_teaching_assignments", "c38_assignment_retirement"): ("c38_retire_teaching_identity", 9, "AFTER DELETE", None),
}

for _table in ("classroom_programs", "classroom_class_states", "classroom_audit_events",
               "classroom_s2_audit_events", "classroom_entry_codes", "classroom_teaching_assignments"):
    TRIGGERS[_table, "c38_no_truncate"] = ("c38_preserve_structural_history", 34, "BEFORE TRUNCATE", None)
for _table in ("classroom_programs", "classroom_class_states", "classroom_audit_events", "classroom_s2_audit_events"):
    TRIGGERS[_table, "c38_preserve_history"] = ("c38_preserve_structural_history", 11, "BEFORE DELETE", None)
for _table in sorted(WITNESSED_DELETE_TABLES):
    TRIGGERS[_table, "c38_witnessed_delete"] = ("c38_guard_residual_delete", 11, "BEFORE DELETE", None)


def install(connection):
    from .classroom_hold_guard import C38_CONSENT_BODY, CONSENT_LOCK, GUARD, retirement_guard_body
    connection.exec_driver_sql(f"CREATE OR REPLACE FUNCTION {CONSENT_LOCK} RETURNS boolean LANGUAGE plpgsql VOLATILE "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET TimeZone='UTC' AS $$" + C38_CONSENT_BODY + "$$")
    connection.exec_driver_sql(f"CREATE OR REPLACE FUNCTION {GUARD} RETURNS trigger LANGUAGE plpgsql VOLATILE "
        "SECURITY INVOKER SET search_path=pg_catalog,pg_temp SET TimeZone='UTC' AS $$" + retirement_guard_body() + "$$")
    for name, (definition, _) in FUNCTIONS.items():
        connection.exec_driver_sql(definition)
        arguments = "text,text,jsonb,jsonb" if name in WITNESS_FUNCTIONS else ""
        connection.exec_driver_sql(f"REVOKE ALL ON FUNCTION public.{name}({arguments}) FROM PUBLIC")
    for (table, trigger), (function, mask, timing, column) in TRIGGERS.items():
        argument = f"'{column}'" if column else ""
        level = "ROW" if mask & 1 else "STATEMENT"
        connection.exec_driver_sql(f"CREATE TRIGGER {trigger} {timing} ON public.{table} "
                                   f"FOR EACH {level} EXECUTE FUNCTION public.{function}({argument})")


def validate(connection, error):
    reason = "classroom_residual_guard_unqualified"
    functions = connection.execute(text("""
        SELECT p.proname,p.prosrc,p.prosecdef,p.proconfig,p.provolatile,p.pronargs,
          NOT EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                      LEFT JOIN pg_roles role ON role.oid = a.grantee
                      WHERE a.grantee <> p.proowner AND a.privilege_type='EXECUTE'
                      AND (a.is_grantable OR NOT coalesce((
                            role.rolname = 'disposition_custodian' AND p.proname IN ('c38_evidence_scope','c38_require_evidence_witness')
                         OR role.rolname = 'disposition_ordinary' AND p.proname IN ('c38_evidence_scope','c38_require_lifecycle_witness')
                      ),false)))
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN :names
    """).bindparams(bindparam("names", expanding=True)), {"names": tuple(FUNCTIONS)}).all()
    if len(functions) != len(FUNCTIONS):
        raise error(reason)
    for name, body, elevated, config, volatility, args, restricted in functions:
        definition, want_elevated = FUNCTIONS[name]
        if (body != definition.split("$$")[1] or elevated != want_elevated
                or args != (4 if name in WITNESS_FUNCTIONS else 0) or not restricted
                or volatility != "v" or config != ["search_path=pg_catalog, public, pg_temp"]):
            raise error(reason)
    triggers = connection.execute(text("""
        SELECT c.relname,t.tgname,p.proname,t.tgtype,t.tgenabled,t.tgqual IS NULL,
               encode(t.tgargs,'escape'), np.nspname, t.tgattr::text, t.tgisinternal
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace np ON np.oid=p.pronamespace
        WHERE c.relnamespace='public'::regnamespace AND left(t.tgname,4)='c38_'
    """)).all()
    if len(triggers) != len(TRIGGERS):
        raise error(reason)
    for table, name, function, mask, active, unconditional, args, schema, columns, internal in triggers:
        expected = TRIGGERS.get((table, name))
        if (expected is None or (function, mask) != expected[:2] or active != "O"
                or not unconditional or schema != "public" or columns or internal
                or args != (expected[3] + "\\000" if expected[3] else "")):
            raise error(reason)
