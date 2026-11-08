"""Frozen additive C36 saved-assignment contract."""
C36 = {'classroom_choice_assignments': {'exact': True,
                                  'columns': {'id': ('Integer', None, False),
                                              'program_id': ('Integer', None, False),
                                              'class_id': ('Integer', None, False),
                                              'week_start': ('Date', None, False),
                                              'created_at': ('DateTime', None, False),
                                              'request_key': ('String', 32, False),
                                              'current_version': ('Integer', None, False)},
                                  'defaults': {'id': 'serial'},
                                  'checks': {'ck_classroom_choice_request': 'length(request_key) = 32',
                                             'ck_classroom_choice_version': 'current_version > 0'},
                                  'pg_checks': {'ck_classroom_choice_request': 'length(request_key::text) = '
                                                                               '32',
                                                'ck_classroom_choice_version': 'current_version > 0'},
                                  'foreign_keys': [(('class_id', 'program_id'),
                                                    'classroom_classes',
                                                    ('id', 'program_id'),
                                                    'RESTRICT')],
                                  'primary_key': ('id',),
                                  'unique': {'uq_classroom_choice_scope': ('id', 'class_id', 'program_id'),
                                             'uq_classroom_choice_request': ('class_id', 'request_key')},
                                  'indexes': {'ix_classroom_choice_week': (('class_id', 'week_start', 'id'), False, None)}},
 'classroom_choice_versions': {'exact': True,
                               'columns': {'assignment_id': ('Integer', None, False),
                                           'version': ('Integer', None, False),
                                           'program_id': ('Integer', None, False),
                                           'class_id': ('Integer', None, False),
                                           'author_assignment_id': ('Integer', None, False),
                                           'authored_at': ('DateTime', None, False),
                                           'state': ('String', 12, False),
                                           'content': ('JSON', None, False)},
                               'defaults': {},
                               'checks': {'ck_classroom_choice_history_version': 'version > 0',
                                          'ck_classroom_choice_state': "state IN ('active', 'cancelled')"},
                               'pg_checks': {'ck_classroom_choice_history_version': 'version > 0',
                                             'ck_classroom_choice_state': 'state::text = ANY '
                                                                          "(ARRAY['active'::character "
                                                                          "varying, 'cancelled'::character "
                                                                          'varying]::text[])'},
                               'foreign_keys': [(('assignment_id', 'class_id', 'program_id'),
                                                 'classroom_choice_assignments',
                                                 ('id', 'class_id', 'program_id'),
                                                 'RESTRICT'),
                                                (('author_assignment_id', 'class_id', 'program_id'),
                                                 'classroom_teaching_assignments',
                                                 ('id', 'class_id', 'program_id'),
                                                 'RESTRICT')],
                               'primary_key': ('assignment_id', 'version'),
                               'unique': {},
                               'indexes': {}}}

# Observed with an independent PostgreSQL dump/restore. Accept only this exact
# expression at this exact table/constraint; retain all casts and Boolean terms.
POSTGRES_RESTORE_CHECK_FORMS = {
    ('classroom_choice_versions', 'ck_classroom_choice_state'):
        "state::text = ANY (ARRAY['active'::character varying::text, 'cancelled'::character varying::text])",
}
