"""Narrow installed-schema contract shared by the calendar and PTA operators.

This is the frozen c22 extension contract, independent of live ORM metadata,
feature flags, and plan/receipt versions. The existing operator-specific p21
checks remain in their callers. Do not use this for historical seasonal repair.
"""
from __future__ import annotations

import re

from sqlalchemy import INTEGER, DateTime, Integer, MetaData, String, Table, inspect, select, text

SUPPORTED_REVISIONS = ("p21team001", "c22class001")

# Required c22 columns, keys, relationships, and indexes as installed by the
# reviewed additive migration. PostgreSQL deparses CHECK expressions differently;
# retaining its explicit forms preserves Boolean grouping during comparison.
_C22 = {
    'classroom_programs': {
        'columns': {
            'organization_id': ('Integer', None, False), 'owner_verifier_id': ('Integer', None, False), 'owner_version': ('Integer', None, False),
            'created_at': ('DateTime', None, False),
        },
        'checks': {'ck_classroom_program_owner_version': 'owner_version >= 1'},
        'foreign_keys': [
            (('organization_id',), 'organizations', ('id',), 'RESTRICT'),
            (('owner_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
        ],
        'primary_key': ('organization_id',),
        'unique': {},
        'indexes': {'ix_classroom_programs_owner_verifier_id': (('owner_verifier_id',), False, None)},
        'pg_checks': {'ck_classroom_program_owner_version': 'owner_version >= 1'},
    },
    'classroom_classes': {
        'columns': {
            'id': ('Integer', None, False), 'program_id': ('Integer', None, False), 'display_name': ('String', 150, False),
            'created_at': ('DateTime', None, False),
        },
        'checks': {'ck_classroom_class_name': 'length(trim(display_name)) BETWEEN 1 AND 150'},
        'foreign_keys': [(('program_id',), 'classroom_programs', ('organization_id',), 'RESTRICT')],
        'primary_key': ('id',),
        'unique': {'uq_classroom_class_id_program': ('id', 'program_id')},
        'indexes': {'ix_classroom_classes_program_id': (('program_id',), False, None)},
        'pg_checks': {'ck_classroom_class_name': 'length(TRIM(BOTH FROM display_name)) >= 1 AND length(TRIM(BOTH FROM display_name)) <= 150'},
    },
    'classroom_ownership_transfers': {
        'columns': {
            'id': ('Integer', None, False), 'program_id': ('Integer', None, False), 'initiator_verifier_id': ('Integer', None, False),
            'recipient_verifier_id': ('Integer', None, False), 'owner_version': ('Integer', None, False), 'status': ('String', 12, False),
            'created_at': ('DateTime', None, False), 'resolved_at': ('DateTime', None, True),
        },
        'checks': {
            'ck_classroom_transfer_resolution':
                "(status = 'pending' AND resolved_at IS NULL) OR (status <> 'pending' AND resolved_at IS NOT NULL AND resolved_at >= created_at)",
            'ck_classroom_transfer_status': "status IN ('pending', 'accepted', 'cancelled', 'superseded')",
            'ck_classroom_transfer_distinct_adults': 'initiator_verifier_id <> recipient_verifier_id',
            'ck_classroom_transfer_owner_version': 'owner_version >= 1',
        },
        'foreign_keys': [
            (('initiator_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('program_id',), 'classroom_programs', ('organization_id',), 'RESTRICT'),
            (('recipient_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
        ],
        'primary_key': ('id',),
        'unique': {'uq_classroom_transfer_id_program': ('id', 'program_id')},
        'indexes': {'uq_classroom_transfer_pending': (('program_id',), True, "status = 'pending'")},
        'pg_checks': {
            'ck_classroom_transfer_distinct_adults': 'initiator_verifier_id <> recipient_verifier_id',
            'ck_classroom_transfer_owner_version': 'owner_version >= 1',
            'ck_classroom_transfer_resolution':
                "status::text = 'pending'::text AND resolved_at IS NULL OR status::text <> 'pending'::text AND resolved_at IS NOT NULL AND "
                'resolved_at >= created_at',
            'ck_classroom_transfer_status':
                "status::text = ANY (ARRAY['pending'::character varying, 'accepted'::character varying, 'cancelled'::character varying, "
                "'superseded'::character varying]::text[])",
        },
    },
    'classroom_role_grants': {
        'columns': {
            'id': ('Integer', None, False), 'program_id': ('Integer', None, False), 'verifier_id': ('Integer', None, False),
            'role': ('String', 20, False), 'starts_at': ('DateTime', None, False), 'ended_at': ('DateTime', None, True),
            'ended_reason': ('String', 10, True), 'granted_by_verifier_id': ('Integer', None, False), 'ended_by_verifier_id': ('Integer', None, True),
        },
        'checks': {
            'ck_classroom_role_end_evidence':
                '(ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL) OR (ended_at IS NOT NULL AND ended_by_verifier_id IS '
                "NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('revoked', 'departed'))",
            'ck_classroom_role_kind': "role IN ('head_director', 'admin', 'code_manager', 'billing')",
            'ck_classroom_role_period': 'ended_at IS NULL OR ended_at > starts_at',
        },
        'foreign_keys': [
            (('ended_by_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('granted_by_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('program_id',), 'classroom_programs', ('organization_id',), 'RESTRICT'),
            (('verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
        ],
        'primary_key': ('id',),
        'unique': {'uq_classroom_role_id_program': ('id', 'program_id')},
        'indexes': {
            'ix_classroom_role_grants_verifier_id': (('verifier_id',), False, None),
            'uq_classroom_role_open': (('program_id', 'verifier_id', 'role'), True, 'ended_at IS NULL'),
        },
        'pg_checks': {
            'ck_classroom_role_end_evidence':
                'ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL OR ended_at IS NOT NULL AND ended_by_verifier_id IS NOT'
                " NULL AND ended_reason IS NOT NULL AND (ended_reason::text = ANY (ARRAY['revoked'::character varying, 'departed'::character "
                'varying]::text[]))',
            'ck_classroom_role_kind':
                "role::text = ANY (ARRAY['head_director'::character varying, 'admin'::character varying, 'code_manager'::character varying, "
                "'billing'::character varying]::text[])",
            'ck_classroom_role_period': 'ended_at IS NULL OR ended_at > starts_at',
        },
    },
    'classroom_student_memberships': {
        'columns': {
            'id': ('Integer', None, False), 'class_id': ('Integer', None, False), 'profile_id': ('Integer', None, False),
            'created_at': ('DateTime', None, False),
        },
        'checks': {},
        'foreign_keys': [(('class_id',), 'classroom_classes', ('id',), 'RESTRICT'), (('profile_id',), 'woodchuck_profiles', ('id',), 'RESTRICT')],
        'primary_key': ('id',),
        'unique': {'uq_classroom_membership_class_student': ('class_id', 'profile_id')},
        'indexes': {'ix_classroom_student_memberships_profile_id': (('profile_id',), False, None)},
        'pg_checks': {},
    },
    'classroom_teaching_assignments': {
        'columns': {
            'id': ('Integer', None, False), 'program_id': ('Integer', None, False), 'class_id': ('Integer', None, False),
            'verifier_id': ('Integer', None, False), 'starts_at': ('DateTime', None, False), 'ended_at': ('DateTime', None, True),
            'ended_reason': ('String', 10, True), 'granted_by_verifier_id': ('Integer', None, False), 'ended_by_verifier_id': ('Integer', None, True),
        },
        'checks': {
            'ck_classroom_assignment_end_evidence':
                '(ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL) OR (ended_at IS NOT NULL AND ended_by_verifier_id IS '
                "NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('revoked', 'departed'))",
            'ck_classroom_assignment_period': 'ended_at IS NULL OR ended_at > starts_at',
        },
        'foreign_keys': [
            (('class_id', 'program_id'), 'classroom_classes', ('id', 'program_id'), 'RESTRICT'),
            (('ended_by_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('granted_by_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
        ],
        'primary_key': ('id',),
        'unique': {'uq_classroom_assignment_scope': ('id', 'class_id', 'program_id')},
        'indexes': {
            'ix_classroom_assignment_program_verifier': (('program_id', 'verifier_id'), False, None),
            'uq_classroom_assignment_open': (('class_id', 'verifier_id'), True, 'ended_at IS NULL'),
        },
        'pg_checks': {
            'ck_classroom_assignment_end_evidence':
                'ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL OR ended_at IS NOT NULL AND ended_by_verifier_id IS NOT'
                " NULL AND ended_reason IS NOT NULL AND (ended_reason::text = ANY (ARRAY['revoked'::character varying, 'departed'::character "
                'varying]::text[]))',
            'ck_classroom_assignment_period': 'ended_at IS NULL OR ended_at > starts_at',
        },
    },
    'classroom_audit_events': {
        'columns': {
            'id': ('Integer', None, False), 'program_id': ('Integer', None, False), 'class_id': ('Integer', None, True),
            'actor_verifier_id': ('Integer', None, False), 'target_verifier_id': ('Integer', None, True), 'action': ('String', 30, False),
            'role_grant_id': ('Integer', None, True), 'assignment_id': ('Integer', None, True), 'transfer_id': ('Integer', None, True),
            'old_owner_id': ('Integer', None, True), 'new_owner_id': ('Integer', None, True), 'occurred_at': ('DateTime', None, False),
        },
        'checks': {
            'ck_classroom_audit_class_evidence': "action <> 'class_created' OR class_id IS NOT NULL",
            'ck_classroom_audit_owner_evidence':
                "action <> 'ownership_transferred' OR (old_owner_id IS NOT NULL AND new_owner_id IS NOT NULL AND old_owner_id <> new_owner_id)",
            'ck_classroom_audit_provision_evidence':
                "action <> 'program_provisioned' OR (new_owner_id IS NOT NULL AND target_verifier_id IS NOT NULL)",
            'ck_classroom_audit_action':
                "action IN ('program_provisioned', 'role_granted', 'role_revoked', 'class_created', 'teacher_assigned', 'teacher_revoked', "
                "'teacher_departed', 'transfer_proposed', 'transfer_cancelled', 'transfer_superseded', 'ownership_transferred')",
            'ck_classroom_audit_role_evidence':
                "action NOT IN ('role_granted', 'role_revoked') OR (role_grant_id IS NOT NULL AND target_verifier_id IS NOT NULL)",
            'ck_classroom_audit_teacher_evidence':
                "action NOT IN ('teacher_assigned', 'teacher_revoked', 'teacher_departed') OR (assignment_id IS NOT NULL AND class_id IS NOT NULL "
                'AND target_verifier_id IS NOT NULL)',
            'ck_classroom_audit_transfer_evidence':
                "action NOT IN ('transfer_proposed', 'transfer_cancelled', 'transfer_superseded', 'ownership_transferred') OR (transfer_id IS NOT "
                'NULL AND target_verifier_id IS NOT NULL)',
        },
        'foreign_keys': [
            (('actor_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('assignment_id', 'class_id', 'program_id'), 'classroom_teaching_assignments', ('id', 'class_id', 'program_id'), 'RESTRICT'),
            (('class_id', 'program_id'), 'classroom_classes', ('id', 'program_id'), 'RESTRICT'),
            (('new_owner_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('old_owner_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('program_id',), 'classroom_programs', ('organization_id',), 'RESTRICT'),
            (('role_grant_id', 'program_id'), 'classroom_role_grants', ('id', 'program_id'), 'RESTRICT'),
            (('target_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('transfer_id', 'program_id'), 'classroom_ownership_transfers', ('id', 'program_id'), 'RESTRICT'),
        ],
        'primary_key': ('id',),
        'unique': {},
        'indexes': {'ix_classroom_audit_program_time': (('program_id', 'occurred_at'), False, None)},
        'pg_checks': {
            'ck_classroom_audit_action':
                "action::text = ANY (ARRAY['program_provisioned'::character varying, 'role_granted'::character varying, 'role_revoked'::character "
                "varying, 'class_created'::character varying, 'teacher_assigned'::character varying, 'teacher_revoked'::character varying, "
                "'teacher_departed'::character varying, 'transfer_proposed'::character varying, 'transfer_cancelled'::character varying, "
                "'transfer_superseded'::character varying, 'ownership_transferred'::character varying]::text[])",
            'ck_classroom_audit_class_evidence': "action::text <> 'class_created'::text OR class_id IS NOT NULL",
            'ck_classroom_audit_owner_evidence':
                "action::text <> 'ownership_transferred'::text OR old_owner_id IS NOT NULL AND new_owner_id IS NOT NULL AND old_owner_id <> "
                'new_owner_id',
            'ck_classroom_audit_provision_evidence':
                "action::text <> 'program_provisioned'::text OR new_owner_id IS NOT NULL AND target_verifier_id IS NOT NULL",
            'ck_classroom_audit_role_evidence':
                "(action::text <> ALL (ARRAY['role_granted'::character varying, 'role_revoked'::character varying]::text[])) OR role_grant_id IS "
                'NOT NULL AND target_verifier_id IS NOT NULL',
            'ck_classroom_audit_teacher_evidence':
                "(action::text <> ALL (ARRAY['teacher_assigned'::character varying, 'teacher_revoked'::character varying, "
                "'teacher_departed'::character varying]::text[])) OR assignment_id IS NOT NULL AND class_id IS NOT NULL AND target_verifier_id IS "
                'NOT NULL',
            'ck_classroom_audit_transfer_evidence':
                "(action::text <> ALL (ARRAY['transfer_proposed'::character varying, 'transfer_cancelled'::character varying, "
                "'transfer_superseded'::character varying, 'ownership_transferred'::character varying]::text[])) OR transfer_id IS NOT NULL AND "
                'target_verifier_id IS NOT NULL',
        },
    },
    'classroom_membership_periods': {
        'columns': {
            'id': ('Integer', None, False), 'membership_id': ('Integer', None, False), 'starts_at': ('DateTime', None, False),
            'ended_at': ('DateTime', None, True), 'state': ('String', 10, False), 'ended_reason': ('String', 10, True),
        },
        'checks': {
            'ck_classroom_membership_period_end':
                "(ended_at IS NULL AND ended_reason IS NULL) OR (ended_at IS NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('departed',"
                " 'changed'))",
            'ck_classroom_membership_period_state': "state IN ('active', 'held')",
            'ck_classroom_membership_period_dates': 'ended_at IS NULL OR ended_at > starts_at',
        },
        'foreign_keys': [(('membership_id',), 'classroom_student_memberships', ('id',), 'RESTRICT')],
        'primary_key': ('id',),
        'unique': {'uq_classroom_membership_period_start': ('membership_id', 'starts_at')},
        'indexes': {'uq_classroom_membership_period_open': (('membership_id',), True, 'ended_at IS NULL')},
        'pg_checks': {
            'ck_classroom_membership_period_dates': 'ended_at IS NULL OR ended_at > starts_at',
            'ck_classroom_membership_period_end':
                'ended_at IS NULL AND ended_reason IS NULL OR ended_at IS NOT NULL AND ended_reason IS NOT NULL AND (ended_reason::text = ANY '
                "(ARRAY['departed'::character varying, 'changed'::character varying]::text[]))",
            'ck_classroom_membership_period_state': "state::text = ANY (ARRAY['active'::character varying, 'held'::character varying]::text[])",
        },
    },
}


CLASSROOM_TABLES = tuple(_C22)


def _sql(value):
    # Remove formatting outside literals only. Parentheses, operators, casts,
    # and literal contents remain significant (including AND/OR grouping).
    return "".join(token if token.startswith("'") else re.sub(r"\s+", "", token).lower()
                   for token in re.split(r"('(?:''|[^'])*')", str(value)))


def installed_revision(connection, error):
    """Validate the single installed revision and its additive extension."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    revisions = (list(connection.scalars(text("SELECT version_num FROM alembic_version")))
                 if "alembic_version" in tables else [])
    if len(revisions) != 1 or revisions[0] not in SUPPORTED_REVISIONS:
        raise error("revision_not_approved: require exactly one of p21team001, c22class001")
    revision = revisions[0]
    if revision == "p21team001":
        return revision
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise error("classroom_schema_backend_not_supported")
    schema = connection.scalar(text("SELECT current_schema()")) if dialect == "postgresql" else None
    if dialect == "postgresql":
        # Reflection also reports NOT VALID / deferred constraints and invalid
        # indexes. Their names/definitions alone do not establish enforcement.
        if connection.scalar(text("""
            SELECT EXISTS (
              SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
              JOIN pg_namespace n ON n.oid=t.relnamespace
              WHERE n.nspname=current_schema() AND t.relname=ANY(:tables)
                AND (NOT c.convalidated OR c.condeferrable OR c.condeferred))
            OR EXISTS (
              SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
              JOIN pg_namespace n ON n.oid=t.relnamespace
              WHERE n.nspname=current_schema() AND t.relname=ANY(:tables)
                AND (NOT i.indisvalid OR NOT i.indisready))
        """), {"tables": list(CLASSROOM_TABLES)}):
            raise error("classroom_schema_missing_or_changed: constraint_or_index_enforcement")
    for name, expected in _C22.items():
        def refuse(part):
            raise error("classroom_schema_missing_or_changed: " + name + ": " + part)
        if name not in tables:
            refuse("table")
        columns = {c["name"]: c for c in inspector.get_columns(name)}
        # SQLite reflection normalizes INT/affinity aliases to INTEGER. SQLite
        # itself reports the genuine rowid-compatible type as exactly INTEGER.
        sqlite_types = dict(connection.execute(text(
            "SELECT name, type FROM pragma_table_xinfo(:table, 'main')"
        ), {"table": name}).all()) if dialect == "sqlite" else {}
        for column, (kind, length, nullable) in expected["columns"].items():
            actual = columns.get(column)
            types = {"Integer": Integer, "String": String, "DateTime": DateTime}
            if (actual is None or actual["nullable"] != nullable
                    or not isinstance(actual["type"], types[kind])
                    # SMALLINT/BIGINT inherit Integer; require physical INTEGER.
                    or (kind == "Integer" and (type(actual["type"]) is not INTEGER
                        or (dialect == "sqlite" and sqlite_types.get(column, "") != "INTEGER")))
                    or (kind == "String" and actual["type"].length != length)
                    or (kind == "DateTime" and dialect == "postgresql" and not actual["type"].timezone)):
                refuse("columns")
        if tuple(inspector.get_pk_constraint(name)["constrained_columns"]) != expected["primary_key"]:
            refuse("primary_key")
        checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints(name)}
        expected_checks = expected["pg_checks"] if dialect == "postgresql" else expected["checks"]
        if any(_sql(checks.get(key, "")) != _sql(sql) for key, sql in expected_checks.items()):
            refuse("checks")
        unique = {c["name"]: tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
        if any(unique.get(key) != columns for key, columns in expected["unique"].items()):
            refuse("unique")
        foreign_keys = {(tuple(f["constrained_columns"]), f["referred_table"],
                         tuple(f["referred_columns"]), f.get("options", {}).get("ondelete", "").upper())
                        for f in inspector.get_foreign_keys(name)
                        if f.get("referred_schema") in {None, schema}
                        and not f.get("options", {}).get("deferrable")}
        if set(expected["foreign_keys"]) - foreign_keys:
            refuse("foreign_keys")
        indexes = {i["name"]: i for i in inspector.get_indexes(name)}
        for key, (columns, unique, predicate) in expected["indexes"].items():
            index = indexes.get(key)
            if index is None or tuple(index["column_names"]) != columns or bool(index["unique"]) != unique:
                refuse("indexes")
            actual_predicate = index.get("dialect_options", {}).get(dialect + "_where")
            # These predicates contain one comparison only. PostgreSQL wraps it
            # and adds a text cast; there is no Boolean grouping to erase here.
            def predicate_shape(value):
                return "".join(token if token.startswith("'") else
                               re.sub(r"[()]", "", token.replace("::text", ""))
                               for token in re.split(r"('(?:''|[^'])*')", _sql(value)))
            if predicate_shape(actual_predicate) != predicate_shape(predicate):
                refuse("indexes")
    return revision


def classroom_snapshot(connection, revision):
    """Separate transactional preservation evidence, never old receipt coverage."""
    if revision != "c22class001":
        return {}
    metadata = MetaData()
    tables = [Table(name, metadata, autoload_with=connection, resolve_fks=False)
              for name in CLASSROOM_TABLES]
    return {table.name: [tuple(row) for row in connection.execute(
        select(table).order_by(*table.primary_key.columns))] for table in tables}


def assert_classroom_unchanged(connection, revision, before, error):
    if classroom_snapshot(connection, revision) != before:
        raise error("unexpected_classroom_row_change: transaction_rolled_back")
