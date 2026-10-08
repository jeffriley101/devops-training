"""Frozen c24 reporting schema contract, independent of runtime ORM metadata.

Explicit SQLite and PostgreSQL reflection was reviewed against c24 migration DDL.
"""
from sqlalchemy import text


C24 = {'classroom_reporting_periods': {'exact': True,
                                 'columns': {'id': ('Integer', None, False),
                                             'program_id': ('Integer', None, False),
                                             'class_id': ('Integer', None, False),
                                             'membership_id': ('Integer', None, False),
                                             'membership_period_id': ('Integer', None, False),
                                             'profile_id': ('Integer', None, False),
                                             'authorizer_profile_id': ('Integer', None, False),
                                             'account_declared_at': ('DateTime', None, False),
                                             'consent_id': ('Integer', None, True),
                                             'entitlement_id': ('Integer', None, False),
                                             'class_activation_at': ('DateTime', None, False),
                                             'source_audit_id': ('Integer', None, True),
                                             'scope_version': ('String', 80, False),
                                             'notice_version': ('String', 80, False),
                                             'starts_at': ('DateTime', None, False),
                                             'ended_at': ('DateTime', None, True),
                                             'ended_by_profile_id': ('Integer', None, True),
                                             'ended_reason': ('String', 30, True),
                                             'created_at': ('DateTime', None, False)},
                                 'checks': {'ck_classroom_reporting_authorizer': 'authorizer_profile_id = '
                                                                                 'profile_id',
                                            'ck_classroom_reporting_end': '(ended_at IS NULL AND '
                                                                          'ended_by_profile_id IS NULL AND '
                                                                          'ended_reason IS NULL) OR '
                                                                          '(ended_at IS NOT NULL AND '
                                                                          'ended_at >= starts_at AND '
                                                                          'ended_by_profile_id IS NOT NULL '
                                                                          'AND ended_reason IS NOT NULL AND '
                                                                          "ended_reason IN ('withdrawn', "
                                                                          "'source_changed'))",
                                            'ck_classroom_reporting_notice_version': 'length(trim(notice_version)) '
                                                                                     'BETWEEN 1 AND 80',
                                            'ck_classroom_reporting_revoker': 'ended_by_profile_id IS NULL '
                                                                              'OR ended_by_profile_id = '
                                                                              'profile_id',
                                            'ck_classroom_reporting_scope_version': 'length(trim(scope_version)) '
                                                                                    'BETWEEN 1 AND 80'},
                                 'foreign_keys': [(('authorizer_profile_id',),
                                                   'woodchuck_profiles',
                                                   ('id',),
                                                   'RESTRICT'),
                                                  (('class_id', 'program_id'),
                                                   'classroom_classes',
                                                   ('id', 'program_id'),
                                                   'RESTRICT'),
                                                  (('consent_id',),
                                                   'child_consent_evidence',
                                                   ('id',),
                                                   'RESTRICT'),
                                                  (('ended_by_profile_id',),
                                                   'woodchuck_profiles',
                                                   ('id',),
                                                   'RESTRICT'),
                                                  (('entitlement_id', 'program_id'),
                                                   'classroom_entitlements',
                                                   ('id', 'program_id'),
                                                   'RESTRICT'),
                                                  (('membership_id',),
                                                   'classroom_student_memberships',
                                                   ('id',),
                                                   'RESTRICT'),
                                                  (('membership_period_id',),
                                                   'classroom_membership_periods',
                                                   ('id',),
                                                   'RESTRICT'),
                                                  (('profile_id',),
                                                   'woodchuck_profiles',
                                                   ('id',),
                                                   'RESTRICT'),
                                                  (('source_audit_id',),
                                                   'classroom_s2_audit_events',
                                                   ('id',),
                                                   'RESTRICT')],
                                 'primary_key': ('id',),
                                 'unique': {'uq_classroom_reporting_scope': ('id',
                                                                             'program_id',
                                                                             'class_id',
                                                                             'profile_id')},
                                 'indexes': {'ix_classroom_reporting_student_class': (('profile_id',
                                                                                       'class_id'),
                                                                                      False,
                                                                                      None),
                                             'uq_classroom_reporting_open': (('membership_id',),
                                                                             True,
                                                                             'ended_at IS NULL')},
                                 'defaults': {'id': 'serial', 'created_at': 'clock'},
                                 'pg_checks': {'ck_classroom_reporting_authorizer': 'authorizer_profile_id = '
                                                                                    'profile_id',
                                               'ck_classroom_reporting_end': 'ended_at IS NULL AND '
                                                                             'ended_by_profile_id IS NULL '
                                                                             'AND ended_reason IS NULL OR '
                                                                             'ended_at IS NOT NULL AND '
                                                                             'ended_at >= starts_at AND '
                                                                             'ended_by_profile_id IS NOT '
                                                                             'NULL AND ended_reason IS NOT '
                                                                             'NULL AND (ended_reason::text = '
                                                                             'ANY '
                                                                             "(ARRAY['withdrawn'::character "
                                                                             'varying, '
                                                                             "'source_changed'::character "
                                                                             'varying]::text[]))',
                                               'ck_classroom_reporting_notice_version': 'length(TRIM(BOTH '
                                                                                        'FROM '
                                                                                        'notice_version)) >= '
                                                                                        '1 AND '
                                                                                        'length(TRIM(BOTH '
                                                                                        'FROM '
                                                                                        'notice_version)) <= '
                                                                                        '80',
                                               'ck_classroom_reporting_revoker': 'ended_by_profile_id IS '
                                                                                 'NULL OR '
                                                                                 'ended_by_profile_id = '
                                                                                 'profile_id',
                                               'ck_classroom_reporting_scope_version': 'length(TRIM(BOTH '
                                                                                       'FROM scope_version)) '
                                                                                       '>= 1 AND '
                                                                                       'length(TRIM(BOTH '
                                                                                       'FROM scope_version)) '
                                                                                       '<= 80'}},
 'classroom_s3_audit_events': {'exact': True,
                               'columns': {'id': ('Integer', None, False),
                                           'program_id': ('Integer', None, False),
                                           'class_id': ('Integer', None, False),
                                           'profile_id': ('Integer', None, False),
                                           'period_id': ('Integer', None, False),
                                           'actor_profile_id': ('Integer', None, False),
                                           'action': ('String', 30, False),
                                           'scope_version': ('String', 80, False),
                                           'notice_version': ('String', 80, False),
                                           'reason': ('String', 30, True),
                                           'occurred_at': ('DateTime', None, False)},
                               'checks': {'ck_classroom_s3_audit_action': "action IN ('reporting_granted', "
                                                                          "'reporting_withdrawn', "
                                                                          "'reporting_reauthorized')",
                                          'ck_classroom_s3_audit_actor': 'actor_profile_id = profile_id',
                                          'ck_classroom_s3_audit_notice_version': 'length(trim(notice_version)) '
                                                                                  'BETWEEN 1 AND 80',
                                          'ck_classroom_s3_audit_reason': "(action = 'reporting_withdrawn' "
                                                                          'AND reason IS NOT NULL AND reason '
                                                                          "IN ('withdrawn', "
                                                                          "'source_changed')) OR (action <> "
                                                                          "'reporting_withdrawn' AND reason "
                                                                          'IS NULL)',
                                          'ck_classroom_s3_audit_scope_version': 'length(trim(scope_version)) '
                                                                                 'BETWEEN 1 AND 80'},
                               'foreign_keys': [(('actor_profile_id',),
                                                 'woodchuck_profiles',
                                                 ('id',),
                                                 'RESTRICT'),
                                                (('period_id', 'program_id', 'class_id', 'profile_id'),
                                                 'classroom_reporting_periods',
                                                 ('id', 'program_id', 'class_id', 'profile_id'),
                                                 'RESTRICT')],
                               'primary_key': ('id',),
                               'unique': {},
                               'indexes': {'ix_classroom_s3_audit_program_time': (('program_id',
                                                                                   'occurred_at'),
                                                                                  False,
                                                                                  None)},
                               'defaults': {'id': 'serial', 'occurred_at': 'clock'},
                               'pg_checks': {'ck_classroom_s3_audit_action': 'action::text = ANY '
                                                                             "(ARRAY['reporting_granted'::character "
                                                                             'varying, '
                                                                             "'reporting_withdrawn'::character "
                                                                             'varying, '
                                                                             "'reporting_reauthorized'::character "
                                                                             'varying]::text[])',
                                             'ck_classroom_s3_audit_actor': 'actor_profile_id = profile_id',
                                             'ck_classroom_s3_audit_notice_version': 'length(TRIM(BOTH FROM '
                                                                                     'notice_version)) >= 1 '
                                                                                     'AND length(TRIM(BOTH '
                                                                                     'FROM notice_version)) '
                                                                                     '<= 80',
                                             'ck_classroom_s3_audit_reason': 'action::text = '
                                                                             "'reporting_withdrawn'::text "
                                                                             'AND reason IS NOT NULL AND '
                                                                             '(reason::text = ANY '
                                                                             "(ARRAY['withdrawn'::character "
                                                                             'varying, '
                                                                             "'source_changed'::character "
                                                                             'varying]::text[])) OR '
                                                                             'action::text <> '
                                                                             "'reporting_withdrawn'::text "
                                                                             'AND reason IS NULL',
                                             'ck_classroom_s3_audit_scope_version': 'length(TRIM(BOTH FROM '
                                                                                    'scope_version)) >= 1 '
                                                                                    'AND length(TRIM(BOTH '
                                                                                    'FROM scope_version)) <= '
                                                                                    '80'}}}


SQLITE_REPORTING_INSERT = "CREATE TRIGGER classroom_reporting_no_overlap_insert\nBEFORE INSERT ON classroom_reporting_periods\nFOR EACH ROW WHEN EXISTS (\n    SELECT 1 FROM classroom_reporting_periods p\n    WHERE p.membership_id = NEW.membership_id\n      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)\n      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)\n)\nBEGIN SELECT RAISE(ABORT, 'classroom_reporting_period_overlap'); END"

SQLITE_REPORTING_UPDATE = "CREATE TRIGGER classroom_reporting_no_overlap_update\nBEFORE UPDATE ON classroom_reporting_periods\nFOR EACH ROW WHEN EXISTS (\n    SELECT 1 FROM classroom_reporting_periods p\n    WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id\n      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)\n      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)\n)\nBEGIN SELECT RAISE(ABORT, 'classroom_reporting_period_overlap'); END"

POSTGRES_REPORTING_FUNCTION = "CREATE FUNCTION classroom_reporting_no_overlap() RETURNS trigger\nLANGUAGE plpgsql AS $$\nBEGIN\n    IF TG_OP = 'UPDATE' THEN\n        -- PostgreSQL locks an UPDATE target tuple before BEFORE ROW triggers.\n        -- Never wait for its parents in that state: supported writers already\n        -- hold these locks; uncoordinated DML must fail instead of deadlocking.\n        PERFORM organization_id FROM classroom_programs\n        WHERE organization_id = NEW.program_id FOR UPDATE NOWAIT;\n        PERFORM id FROM classroom_classes\n        WHERE id = NEW.class_id AND program_id = NEW.program_id FOR UPDATE NOWAIT;\n        PERFORM id FROM classroom_student_memberships\n        WHERE id = NEW.membership_id FOR UPDATE NOWAIT;\n    ELSE\n        PERFORM organization_id FROM classroom_programs\n        WHERE organization_id = NEW.program_id FOR UPDATE;\n        PERFORM id FROM classroom_classes\n        WHERE id = NEW.class_id AND program_id = NEW.program_id FOR UPDATE;\n        PERFORM id FROM classroom_student_memberships\n        WHERE id = NEW.membership_id FOR UPDATE;\n    END IF;\n    IF EXISTS (\n        SELECT 1 FROM classroom_reporting_periods p\n        WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id\n          AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)\n          AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)\n    ) THEN\n        RAISE EXCEPTION 'classroom_reporting_period_overlap' USING ERRCODE = '23514';\n    END IF;\n    RETURN NEW;\nEND;\n$$"


def validate_s3_guards(connection, error):
    """Enforcement state and trigger bodies form part of the reviewed schema."""
    reason = "classroom_schema_missing_or_changed: reporting_interval_guard"
    if connection.dialect.name == "postgresql":
        if connection.scalar(text("""
            SELECT current_setting('session_replication_role') NOT IN ('origin', 'local')
            OR EXISTS (
                SELECT c.oid FROM pg_constraint c
                JOIN pg_class r ON r.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = r.relnamespace
                LEFT JOIN pg_trigger t ON t.tgconstraint = c.oid
                WHERE n.nspname = current_schema() AND r.relname = ANY(:tables)
                  AND c.contype = 'f'
                GROUP BY c.oid
                HAVING count(t.oid) <> 4
                    OR bool_or(NOT t.tgisinternal OR t.tgenabled <> 'O'))
        """), {"tables": list(C24)}):
            raise error("classroom_schema_missing_or_changed: s3_foreign_key_enforcement")
        triggers = list(connection.execute(text("""
            SELECT r.relname, t.tgname FROM pg_trigger t
            JOIN pg_class r ON r.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=r.relnamespace
            WHERE n.nspname=current_schema() AND r.relname=ANY(:tables) AND NOT t.tgisinternal
        """), {"tables": list(C24)}))
        if triggers != [("classroom_reporting_periods", "classroom_reporting_no_overlap")]:
            raise error(reason)
        rows = connection.execute(text("""
            SELECT p.prosrc, p.prosecdef, p.provolatile, p.proconfig,
                   t.tgenabled, t.tgtype, t.tgnargs, t.tgattr::text AS columns,
                   t.tgqual IS NULL AS unconditional, l.lanname,
                   pn.nspname = current_schema() AS local_function
            FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
            JOIN pg_namespace pn ON pn.oid = p.pronamespace
            JOIN pg_language l ON l.oid = p.prolang
            WHERE t.tgrelid = 'classroom_reporting_periods'::regclass
              AND NOT t.tgisinternal AND t.tgname = 'classroom_reporting_no_overlap'
              AND p.proname = 'classroom_reporting_no_overlap'
        """)).mappings().all()
        if len(rows) != 1:
            raise error(reason)
        row = rows[0]
        if (row["prosecdef"] or row["provolatile"] != "v" or row["proconfig"] is not None
                or row["tgenabled"] != "O" or row["tgtype"] != 23 or row["tgnargs"] != 0
                or row["columns"] != "" or not row["unconditional"]
                or row["lanname"] != "plpgsql" or not row["local_function"]
                or row["prosrc"].strip() != POSTGRES_REPORTING_FUNCTION.split("$$")[1].strip()):
            raise error(reason)
    else:
        rows = connection.execute(text("""
            SELECT tbl_name, name, sql FROM sqlite_master
            WHERE type='trigger' AND tbl_name IN ('classroom_reporting_periods', 'classroom_s3_audit_events')
        """)).all()
        found = {name: sql for table, name, sql in rows}
        expected = {"classroom_reporting_no_overlap_insert": SQLITE_REPORTING_INSERT,
                    "classroom_reporting_no_overlap_update": SQLITE_REPORTING_UPDATE}
        if set(found) != set(expected) or any(table != "classroom_reporting_periods" for table, _, _ in rows):
            raise error(reason)
        if any(found[name].strip() != sql.strip() for name, sql in expected.items()):
            raise error(reason)
