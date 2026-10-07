"""Frozen c23 schema and interval guard contract, independent of ORM metadata.

Changes require review of actual migration DDL on both supported databases.
"""
from sqlalchemy import text


C23 = {'classroom_entitlements': {'exact': True,
                            'columns': {'id': ('Integer', None, False),
                                        'program_id': ('Integer', None, False),
                                        'source': ('String', 20, False),
                                        'status': ('String', 10, False),
                                        'starts_at': ('DateTime', None, False),
                                        'ends_at': ('DateTime', None, False),
                                        'class_limit': ('Integer', None, False),
                                        'teacher_limit': ('Integer', None, False),
                                        'approved_by_verifier_id': ('Integer', None, True),
                                        'approved_by_admin': ('String', 64, True),
                                        'provenance': ('String', 100, False),
                                        'created_at': ('DateTime', None, False),
                                        'updated_at': ('DateTime', None, False),
                                        'ended_at': ('DateTime', None, True)},
                            'checks': {'ck_classroom_entitlement_approval': "(source = 'trial' AND "
                                                                            'approved_by_verifier_id IS '
                                                                            'NOT NULL AND '
                                                                            'approved_by_admin IS NULL) '
                                                                            'OR (source = '
                                                                            "'institutional' AND "
                                                                            'approved_by_verifier_id IS '
                                                                            'NULL AND approved_by_admin '
                                                                            'IS NOT NULL)',
                                       'ck_classroom_entitlement_dates': 'ends_at > starts_at',
                                       'ck_classroom_entitlement_end': "(status = 'active' AND ended_at "
                                                                       "IS NULL) OR (status = 'ended' "
                                                                       'AND ended_at IS NOT NULL)',
                                       'ck_classroom_entitlement_limits': 'class_limit >= 0 AND '
                                                                          'teacher_limit >= 0',
                                       'ck_classroom_entitlement_provenance': 'length(trim(provenance)) '
                                                                              'BETWEEN 1 AND 100',
                                       'ck_classroom_entitlement_source': "source IN ('trial', "
                                                                          "'institutional')",
                                       'ck_classroom_entitlement_status': "status IN ('active', "
                                                                          "'ended')"},
                            'foreign_keys': [(('approved_by_verifier_id',),
                                              'trusted_verifiers',
                                              ('id',),
                                              'RESTRICT'),
                                             (('program_id',),
                                              'classroom_programs',
                                              ('organization_id',),
                                              'RESTRICT')],
                            'primary_key': ('id',),
                            'unique': {'uq_classroom_entitlement_scope': ('id', 'program_id')},
                            'indexes': {'ix_classroom_entitlement_program': (('program_id',),
                                                                             False,
                                                                             None),
                                        'uq_classroom_entitlement_trial': (('program_id',),
                                                                           True,
                                                                           "source = 'trial'")},
                            'pg_checks': {'ck_classroom_entitlement_approval': 'source::text = '
                                                                               "'trial'::text AND "
                                                                               'approved_by_verifier_id '
                                                                               'IS NOT NULL AND '
                                                                               'approved_by_admin IS '
                                                                               'NULL OR source::text = '
                                                                               "'institutional'::text "
                                                                               'AND '
                                                                               'approved_by_verifier_id '
                                                                               'IS NULL AND '
                                                                               'approved_by_admin IS '
                                                                               'NOT NULL',
                                          'ck_classroom_entitlement_dates': 'ends_at > starts_at',
                                          'ck_classroom_entitlement_end': 'status::text = '
                                                                          "'active'::text AND ended_at "
                                                                          'IS NULL OR status::text = '
                                                                          "'ended'::text AND ended_at "
                                                                          'IS NOT NULL',
                                          'ck_classroom_entitlement_limits': 'class_limit >= 0 AND '
                                                                             'teacher_limit >= 0',
                                          'ck_classroom_entitlement_provenance': 'length(TRIM(BOTH FROM '
                                                                                 'provenance)) >= 1 AND '
                                                                                 'length(TRIM(BOTH FROM '
                                                                                 'provenance)) <= 100',
                                          'ck_classroom_entitlement_source': 'source::text = ANY '
                                                                             "(ARRAY['trial'::character "
                                                                             'varying, '
                                                                             "'institutional'::character "
                                                                             'varying]::text[])',
                                          'ck_classroom_entitlement_status': 'status::text = ANY '
                                                                             "(ARRAY['active'::character "
                                                                             'varying, '
                                                                             "'ended'::character "
                                                                             'varying]::text[])'},
                            'defaults': {'id': 'serial', 'created_at': 'clock', 'updated_at': 'clock'}},
 'classroom_class_states': {'exact': True,
                            'columns': {'class_id': ('Integer', None, False),
                                        'program_id': ('Integer', None, False),
                                        'state': ('String', 10, False),
                                        'enrollment_open': ('Boolean', None, False),
                                        'changed_by_verifier_id': ('Integer', None, False),
                                        'created_at': ('DateTime', None, False),
                                        'updated_at': ('DateTime', None, False)},
                            'checks': {'ck_classroom_archived_closed': "state <> 'archived' OR "
                                                                       'enrollment_open = false',
                                       'ck_classroom_class_state': "state IN ('active', 'archived')"},
                            'foreign_keys': [(('changed_by_verifier_id',),
                                              'trusted_verifiers',
                                              ('id',),
                                              'RESTRICT'),
                                             (('class_id', 'program_id'),
                                              'classroom_classes',
                                              ('id', 'program_id'),
                                              'RESTRICT')],
                            'primary_key': ('class_id',),
                            'unique': {},
                            'indexes': {'ix_classroom_state_program': (('program_id',), False, None)},
                            'pg_checks': {'ck_classroom_archived_closed': 'state::text <> '
                                                                          "'archived'::text OR "
                                                                          'enrollment_open = false',
                                          'ck_classroom_class_state': 'state::text = ANY '
                                                                      "(ARRAY['active'::character "
                                                                      "varying, 'archived'::character "
                                                                      'varying]::text[])'},
                            'defaults': {'created_at': 'clock', 'updated_at': 'clock'}},
 'classroom_entry_codes': {'exact': True,
                           'columns': {'id': ('Integer', None, False),
                                       'program_id': ('Integer', None, False),
                                       'class_id': ('Integer', None, False),
                                       'generation': ('Integer', None, False),
                                       'digest': ('String', 64, False),
                                       'is_current': ('Boolean', None, False),
                                       'issued_by_verifier_id': ('Integer', None, False),
                                       'issued_at': ('DateTime', None, False),
                                       'revoked_at': ('DateTime', None, True),
                                       'revoked_by_verifier_id': ('Integer', None, True)},
                           'checks': {'ck_classroom_code_digest': 'length(digest) = 64',
                                      'ck_classroom_code_generation': 'generation >= 1',
                                      'ck_classroom_code_revocation': '(is_current = true AND '
                                                                      'revoked_at IS NULL AND '
                                                                      'revoked_by_verifier_id IS NULL) '
                                                                      'OR (is_current = false AND '
                                                                      'revoked_at IS NOT NULL AND '
                                                                      'revoked_by_verifier_id IS NOT '
                                                                      'NULL)'},
                           'foreign_keys': [(('class_id', 'program_id'),
                                             'classroom_classes',
                                             ('id', 'program_id'),
                                             'RESTRICT'),
                                            (('issued_by_verifier_id',),
                                             'trusted_verifiers',
                                             ('id',),
                                             'RESTRICT'),
                                            (('revoked_by_verifier_id',),
                                             'trusted_verifiers',
                                             ('id',),
                                             'RESTRICT')],
                           'primary_key': ('id',),
                           'unique': {'uq_classroom_code_generation': ('class_id', 'generation'),
                                      'uq_classroom_code_scope': ('id', 'class_id', 'program_id')},
                           'indexes': {'uq_classroom_code_current_class': (('class_id',),
                                                                           True,
                                                                           'is_current = true'),
                                       'uq_classroom_code_current_digest': (('program_id', 'digest'),
                                                                            True,
                                                                            'is_current = true')},
                           'pg_checks': {'ck_classroom_code_digest': 'length(digest::text) = 64',
                                         'ck_classroom_code_generation': 'generation >= 1',
                                         'ck_classroom_code_revocation': 'is_current = true AND '
                                                                         'revoked_at IS NULL AND '
                                                                         'revoked_by_verifier_id IS '
                                                                         'NULL OR is_current = false '
                                                                         'AND revoked_at IS NOT NULL '
                                                                         'AND revoked_by_verifier_id IS '
                                                                         'NOT NULL'},
                           'defaults': {'id': 'serial', 'issued_at': 'clock'}},
 'classroom_membership_holds': {'exact': True,
                                'columns': {'membership_id': ('Integer', None, False),
                                            'state': ('String', 12, False),
                                            'reason': ('String', 20, False),
                                            'held_by_verifier_id': ('Integer', None, False),
                                            'held_at': ('DateTime', None, False),
                                            'released_at': ('DateTime', None, True),
                                            'released_by_verifier_id': ('Integer', None, True)},
                                'checks': {'ck_classroom_hold_reason': "reason IN ('conduct', "
                                                                       "'admin_removal')",
                                           'ck_classroom_hold_release': '(released_at IS NULL AND '
                                                                        'released_by_verifier_id IS '
                                                                        'NULL) OR (released_at IS NOT '
                                                                        'NULL AND '
                                                                        'released_by_verifier_id IS NOT '
                                                                        'NULL AND released_at >= '
                                                                        'held_at)',
                                           'ck_classroom_hold_state': "state IN ('suspended', "
                                                                      "'removed')"},
                                'foreign_keys': [(('held_by_verifier_id',),
                                                  'trusted_verifiers',
                                                  ('id',),
                                                  'RESTRICT'),
                                                 (('membership_id',),
                                                  'classroom_student_memberships',
                                                  ('id',),
                                                  'RESTRICT'),
                                                 (('released_by_verifier_id',),
                                                  'trusted_verifiers',
                                                  ('id',),
                                                  'RESTRICT')],
                                'primary_key': ('membership_id',),
                                'unique': {},
                                'indexes': {},
                                'pg_checks': {'ck_classroom_hold_reason': 'reason::text = ANY '
                                                                          "(ARRAY['conduct'::character "
                                                                          'varying, '
                                                                          "'admin_removal'::character "
                                                                          'varying]::text[])',
                                              'ck_classroom_hold_release': 'released_at IS NULL AND '
                                                                           'released_by_verifier_id IS '
                                                                           'NULL OR released_at IS NOT '
                                                                           'NULL AND '
                                                                           'released_by_verifier_id IS '
                                                                           'NOT NULL AND released_at >= '
                                                                           'held_at',
                                              'ck_classroom_hold_state': 'state::text = ANY '
                                                                         "(ARRAY['suspended'::character "
                                                                         "varying, 'removed'::character "
                                                                         'varying]::text[])'},
                                'defaults': {}},
 'classroom_s2_audit_events': {'exact': True,
                               'columns': {'id': ('Integer', None, False),
                                           'program_id': ('Integer', None, False),
                                           'class_id': ('Integer', None, True),
                                           'profile_id': ('Integer', None, True),
                                           'membership_id': ('Integer', None, True),
                                           'actor_verifier_id': ('Integer', None, True),
                                           'actor_profile_id': ('Integer', None, True),
                                           'actor_admin': ('String', 64, True),
                                           'action': ('String', 30, False),
                                           'reason': ('String', 40, True),
                                           'entitlement_id': ('Integer', None, True),
                                           'code_id': ('Integer', None, True),
                                           'occurred_at': ('DateTime', None, False)},
                               'checks': {'ck_classroom_s2_audit_action': "action IN ('trial_started', "
                                                                          "'institutional_created', "
                                                                          "'institutional_changed', "
                                                                          "'institutional_ended', "
                                                                          "'class_activated', "
                                                                          "'class_archived', "
                                                                          "'class_reactivated', "
                                                                          "'enrollment_opened', "
                                                                          "'enrollment_closed', "
                                                                          "'code_issued', "
                                                                          "'code_rotated', "
                                                                          "'code_revoked', "
                                                                          "'student_joined', "
                                                                          "'student_left', "
                                                                          "'student_suspended', "
                                                                          "'student_removed', "
                                                                          "'student_reinstated', "
                                                                          "'teacher_activated', "
                                                                          "'teacher_deactivated')",
                                          'ck_classroom_s2_audit_actor': '(CASE WHEN actor_verifier_id '
                                                                         'IS NOT NULL THEN 1 ELSE 0 END '
                                                                         '+ CASE WHEN actor_profile_id '
                                                                         'IS NOT NULL THEN 1 ELSE 0 END '
                                                                         '+ CASE WHEN actor_admin IS '
                                                                         'NOT NULL THEN 1 ELSE 0 END) = '
                                                                         '1'},
                               'foreign_keys': [(('actor_profile_id',),
                                                 'woodchuck_profiles',
                                                 ('id',),
                                                 'RESTRICT'),
                                                (('actor_verifier_id',),
                                                 'trusted_verifiers',
                                                 ('id',),
                                                 'RESTRICT'),
                                                (('class_id', 'program_id'),
                                                 'classroom_classes',
                                                 ('id', 'program_id'),
                                                 'RESTRICT'),
                                                (('code_id', 'class_id', 'program_id'),
                                                 'classroom_entry_codes',
                                                 ('id', 'class_id', 'program_id'),
                                                 'RESTRICT'),
                                                (('entitlement_id', 'program_id'),
                                                 'classroom_entitlements',
                                                 ('id', 'program_id'),
                                                 'RESTRICT'),
                                                (('membership_id',),
                                                 'classroom_student_memberships',
                                                 ('id',),
                                                 'RESTRICT'),
                                                (('profile_id',),
                                                 'woodchuck_profiles',
                                                 ('id',),
                                                 'RESTRICT'),
                                                (('program_id',),
                                                 'classroom_programs',
                                                 ('organization_id',),
                                                 'RESTRICT')],
                               'primary_key': ('id',),
                               'unique': {},
                               'indexes': {'ix_classroom_s2_audit_program_time': (('program_id',
                                                                                   'occurred_at'),
                                                                                  False,
                                                                                  None)},
                               'pg_checks': {'ck_classroom_s2_audit_action': 'action::text = ANY '
                                                                             "(ARRAY['trial_started'::character "
                                                                             'varying, '
                                                                             "'institutional_created'::character "
                                                                             'varying, '
                                                                             "'institutional_changed'::character "
                                                                             'varying, '
                                                                             "'institutional_ended'::character "
                                                                             'varying, '
                                                                             "'class_activated'::character "
                                                                             'varying, '
                                                                             "'class_archived'::character "
                                                                             'varying, '
                                                                             "'class_reactivated'::character "
                                                                             'varying, '
                                                                             "'enrollment_opened'::character "
                                                                             'varying, '
                                                                             "'enrollment_closed'::character "
                                                                             'varying, '
                                                                             "'code_issued'::character "
                                                                             'varying, '
                                                                             "'code_rotated'::character "
                                                                             'varying, '
                                                                             "'code_revoked'::character "
                                                                             'varying, '
                                                                             "'student_joined'::character "
                                                                             'varying, '
                                                                             "'student_left'::character "
                                                                             'varying, '
                                                                             "'student_suspended'::character "
                                                                             'varying, '
                                                                             "'student_removed'::character "
                                                                             'varying, '
                                                                             "'student_reinstated'::character "
                                                                             'varying, '
                                                                             "'teacher_activated'::character "
                                                                             'varying, '
                                                                             "'teacher_deactivated'::character "
                                                                             'varying]::text[])',
                                             'ck_classroom_s2_audit_actor': '(\n'
                                                                            'CASE\n'
                                                                            '    WHEN actor_verifier_id '
                                                                            'IS NOT NULL THEN 1\n'
                                                                            '    ELSE 0\n'
                                                                            'END +\n'
                                                                            'CASE\n'
                                                                            '    WHEN actor_profile_id '
                                                                            'IS NOT NULL THEN 1\n'
                                                                            '    ELSE 0\n'
                                                                            'END +\n'
                                                                            'CASE\n'
                                                                            '    WHEN actor_admin IS '
                                                                            'NOT NULL THEN 1\n'
                                                                            '    ELSE 0\n'
                                                                            'END) = 1'},
                               'defaults': {'id': 'serial', 'occurred_at': 'clock'}}}

SQLITE_PERIOD_INSERT = """CREATE TRIGGER classroom_period_no_overlap_insert
BEFORE INSERT ON classroom_membership_periods
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM classroom_membership_periods p
    WHERE p.membership_id = NEW.membership_id
      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
)
BEGIN SELECT RAISE(ABORT, 'classroom_membership_period_overlap'); END"""

SQLITE_PERIOD_UPDATE = """CREATE TRIGGER classroom_period_no_overlap_update
BEFORE UPDATE ON classroom_membership_periods
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM classroom_membership_periods p
    WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id
      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
)
BEGIN SELECT RAISE(ABORT, 'classroom_membership_period_overlap'); END"""

POSTGRES_PERIOD_FUNCTION = """CREATE FUNCTION classroom_period_no_overlap() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM p.organization_id FROM classroom_programs p
    JOIN classroom_classes c ON c.program_id = p.organization_id
    JOIN classroom_student_memberships m ON m.class_id = c.id
    WHERE m.id = NEW.membership_id FOR UPDATE OF p;
    PERFORM c.id FROM classroom_classes c
    JOIN classroom_student_memberships m ON m.class_id = c.id
    WHERE m.id = NEW.membership_id FOR UPDATE OF c;
    PERFORM id FROM classroom_student_memberships
    WHERE id = NEW.membership_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM classroom_membership_periods p
        WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id
          AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
          AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
    ) THEN
        RAISE EXCEPTION 'classroom_membership_period_overlap' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$"""


def validate_s2_guards(connection, error):
    """Reject altered S2 behavior as well as reflected constraint definitions."""
    tables = list(C23)
    if connection.dialect.name == "postgresql":
        # Each ordinary FK has two checking triggers on the child and two action
        # triggers on the parent. Inspect both sides through the constraint OID;
        # convalidated alone stays true even after DISABLE TRIGGER ALL.
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
        """), {"tables": tables}):
            raise error("classroom_schema_missing_or_changed: s2_foreign_key_enforcement")
        unexpected = connection.scalar(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_trigger t JOIN pg_class r ON r.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = r.relnamespace
                WHERE n.nspname = current_schema() AND r.relname = ANY(:tables)
                  AND NOT t.tgisinternal)
        """), {"tables": tables})
    else:
        unexpected = set(connection.scalars(text(
            "SELECT tbl_name FROM sqlite_master WHERE type = 'trigger'"))) & set(tables)
    # No user triggers are part of the five new S2 tables' reviewed contract.
    if unexpected:
        raise error("classroom_schema_missing_or_changed: s2_trigger_inventory")
    validate_period_guards(connection, error)


def validate_period_guards(connection, error):
    """The installed revision must enforce the reviewed interval definition."""
    reason = "classroom_schema_missing_or_changed: membership_interval_guard"
    if connection.dialect.name == "postgresql":
        triggers = list(connection.scalars(text("""
            SELECT tgname FROM pg_trigger
            WHERE tgrelid = 'classroom_membership_periods'::regclass AND NOT tgisinternal
        """)))
        if triggers != ["classroom_period_no_overlap"]:
            raise error(reason)
        rows = connection.execute(text("""
            SELECT p.prosrc, p.prosecdef, p.provolatile, p.proconfig,
                   t.tgenabled, t.tgtype, t.tgnargs, t.tgattr::text AS columns,
                   t.tgqual IS NULL AS unconditional, l.lanname,
                   pn.nspname = current_schema() AS local_function
            FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
            JOIN pg_namespace pn ON pn.oid = p.pronamespace
            JOIN pg_language l ON l.oid = p.prolang
            WHERE t.tgrelid = 'classroom_membership_periods'::regclass
              AND NOT t.tgisinternal AND t.tgname = 'classroom_period_no_overlap'
              AND p.proname = 'classroom_period_no_overlap'
        """)).mappings().all()
        if len(rows) != 1:
            raise error(reason)
        row = rows[0]
        if (row["prosecdef"] or row["provolatile"] != "v" or row["proconfig"] is not None
                or row["tgenabled"] != "O" or row["tgtype"] != 23 or row["tgnargs"] != 0
                or row["columns"] != "" or not row["unconditional"]
                or row["lanname"] != "plpgsql" or not row["local_function"]
                or row["prosrc"].strip() != POSTGRES_PERIOD_FUNCTION.split("$$")[1].strip()):
            raise error(reason)
    else:
        found = dict(connection.execute(text("""
            SELECT name, sql FROM sqlite_master
            WHERE type = 'trigger' AND tbl_name = 'classroom_membership_periods'
        """)).all())
        if set(found) != {"classroom_period_no_overlap_insert", "classroom_period_no_overlap_update"}:
            raise error(reason)
        for name, sql in (("classroom_period_no_overlap_insert", SQLITE_PERIOD_INSERT),
                          ("classroom_period_no_overlap_update", SQLITE_PERIOD_UPDATE)):
            if found.get(name, "").strip() != sql.strip():
                raise error(reason)
