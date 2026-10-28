"""Frozen c29 additive authorization contract, independent of runtime ORM and gates."""

C29 = {'classroom_progress_authorizations': {'exact': True,
                                       'columns': {'id': ('Integer', None, False),
                                                   'program_id': ('Integer', None, False),
                                                   'class_id': ('Integer', None, False),
                                                   'profile_id': ('Integer', None, False),
                                                   'membership_id': ('Integer', None, False),
                                                   'membership_period_id': ('Integer', None, False),
                                                   'entitlement_id': ('Integer', None, False),
                                                   'account_declared_at': ('DateTime', None, False),
                                                   'class_activation_at': ('DateTime', None, False),
                                                   'source_audit_id': ('Integer', None, True),
                                                   'scope_version': ('String', 80, False),
                                                   'notice_version': ('String', 80, False),
                                                   'notice_digest': ('String', 64, False),
                                                   'accepted_at': ('DateTime', None, False),
                                                   'accepted_by_profile_id': ('Integer', None, False),
                                                   'acceptance_session_version': ('Integer', None, False),
                                                   'withdrawn_at': ('DateTime', None, True),
                                                   'withdrawn_by_profile_id': ('Integer', None, True),
                                                   'withdrawal_reason': ('String', 12, True)},
                                       'defaults': {'id': 'serial'},
                                       'checks': {'ck_classroom_progress_acceptor': 'accepted_by_profile_id '
                                                                                    '= profile_id',
                                                  'ck_classroom_progress_notice': 'length(trim(notice_version)) '
                                                                                  'BETWEEN 1 AND 80',
                                                  'ck_classroom_progress_notice_digest': 'length(notice_digest) '
                                                                                         '= 64',
                                                  'ck_classroom_progress_scope': 'length(trim(scope_version)) '
                                                                                 'BETWEEN 1 AND 80',
                                                  'ck_classroom_progress_session': 'acceptance_session_version '
                                                                                   '>= 0',
                                                  'ck_classroom_progress_withdrawal': '(withdrawn_at IS NULL '
                                                                                      'AND '
                                                                                      'withdrawn_by_profile_id '
                                                                                      'IS NULL AND '
                                                                                      'withdrawal_reason IS '
                                                                                      'NULL) OR '
                                                                                      '(withdrawn_at IS NOT '
                                                                                      'NULL AND withdrawn_at '
                                                                                      '>= accepted_at AND '
                                                                                      'withdrawn_by_profile_id '
                                                                                      'IS NOT NULL AND '
                                                                                      'withdrawn_by_profile_id '
                                                                                      '= profile_id AND '
                                                                                      'withdrawal_reason IS '
                                                                                      'NOT NULL AND '
                                                                                      'withdrawal_reason IN '
                                                                                      "('withdrawn', "
                                                                                      "'replaced'))"},
                                       'pg_checks': {'ck_classroom_progress_acceptor': 'accepted_by_profile_id '
                                                                                       '= profile_id',
                                                     'ck_classroom_progress_notice': 'length(TRIM(BOTH FROM '
                                                                                     'notice_version)) >= 1 '
                                                                                     'AND length(TRIM(BOTH '
                                                                                     'FROM notice_version)) '
                                                                                     '<= 80',
                                                     'ck_classroom_progress_notice_digest': 'length(notice_digest::text) '
                                                                                            '= 64',
                                                     'ck_classroom_progress_scope': 'length(TRIM(BOTH FROM '
                                                                                    'scope_version)) >= 1 '
                                                                                    'AND length(TRIM(BOTH '
                                                                                    'FROM scope_version)) <= '
                                                                                    '80',
                                                     'ck_classroom_progress_session': 'acceptance_session_version '
                                                                                      '>= 0',
                                                     'ck_classroom_progress_withdrawal': 'withdrawn_at IS '
                                                                                         'NULL AND '
                                                                                         'withdrawn_by_profile_id '
                                                                                         'IS NULL AND '
                                                                                         'withdrawal_reason '
                                                                                         'IS NULL OR '
                                                                                         'withdrawn_at IS '
                                                                                         'NOT NULL AND '
                                                                                         'withdrawn_at >= '
                                                                                         'accepted_at AND '
                                                                                         'withdrawn_by_profile_id '
                                                                                         'IS NOT NULL AND '
                                                                                         'withdrawn_by_profile_id '
                                                                                         '= profile_id AND '
                                                                                         'withdrawal_reason '
                                                                                         'IS NOT NULL AND '
                                                                                         '(withdrawal_reason::text '
                                                                                         '= ANY '
                                                                                         "(ARRAY['withdrawn'::character "
                                                                                         'varying, '
                                                                                         "'replaced'::character "
                                                                                         'varying]::text[]))'},
                                       'primary_key': ('id',),
                                       'unique': {'uq_classroom_progress_scope': ('id',
                                                                                  'class_id',
                                                                                  'program_id')},
                                       'indexes': {'uq_classroom_progress_open': (('profile_id', 'class_id'),
                                                                                  True,
                                                                                  'withdrawn_at IS NULL')},
                                       'foreign_keys': [(('class_id', 'program_id'),
                                                         'classroom_classes',
                                                         ('id', 'program_id'),
                                                         'RESTRICT'),
                                                        (('entitlement_id', 'program_id'),
                                                         'classroom_entitlements',
                                                         ('id', 'program_id'),
                                                         'RESTRICT'),
                                                        (('profile_id',),
                                                         'woodchuck_profiles',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('membership_id',),
                                                         'classroom_student_memberships',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('membership_period_id',),
                                                         'classroom_membership_periods',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('source_audit_id',),
                                                         'classroom_s2_audit_events',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('accepted_by_profile_id',),
                                                         'woodchuck_profiles',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('withdrawn_by_profile_id',),
                                                         'woodchuck_profiles',
                                                         ('id',),
                                                         'RESTRICT')]},
 'classroom_progress_recipients': {'exact': True,
                                   'columns': {'authorization_id': ('Integer', None, False),
                                               'assignment_id': ('Integer', None, False),
                                               'class_id': ('Integer', None, False),
                                               'program_id': ('Integer', None, False)},
                                   'defaults': {},
                                   'checks': {},
                                   'pg_checks': {},
                                   'primary_key': ('authorization_id', 'assignment_id'),
                                   'unique': {},
                                   'indexes': {},
                                   'foreign_keys': [(('authorization_id', 'class_id', 'program_id'),
                                                     'classroom_progress_authorizations',
                                                     ('id', 'class_id', 'program_id'),
                                                     'RESTRICT'),
                                                    (('assignment_id', 'class_id', 'program_id'),
                                                     'classroom_teaching_assignments',
                                                     ('id', 'class_id', 'program_id'),
                                                     'RESTRICT')]}}
