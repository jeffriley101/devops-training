# Frozen C35 school and exact guardian chart reference contract.
C35 = {'classroom_school_authorizations': {'exact': True,
                                     'columns': {'id': ('Integer', None, False),
                                                 'program_id': ('Integer', None, False),
                                                 'approver_id': ('Integer', None, False),
                                                 'authority_kind': ('String', 20, False),
                                                 'school_name': ('String', 150, False),
                                                 'authority_description': ('String', 500, False),
                                                 'contact_name': ('String', 100, False),
                                                 'contact_role': ('String', 100, False),
                                                 'contact_address': ('String', 254, False),
                                                 'contact_source': ('String', 500, False),
                                                 'verification_method': ('String', 20, False),
                                                 'verification_notes': ('String', 1000, False),
                                                 'contacted_at': ('DateTime', None, False),
                                                 'verified_at': ('DateTime', None, False),
                                                 'verified_by_id': ('Integer', None, False),
                                                 'verified_admin_fingerprint': ('String', 64, False),
                                                 'evidence_digest': ('String', 64, False),
                                                 'year_start': ('Integer', None, False),
                                                 'expires_at': ('DateTime', None, False),
                                                 'agreement_version': ('String', 80, False),
                                                 'agreement_digest': ('String', 64, False),
                                                 'approved_at': ('DateTime', None, True),
                                                 'revoked_at': ('DateTime', None, True),
                                                 'revoked_by_id': ('Integer', None, True),
                                                 'revocation_reason': ('String', 24, True)},
                                     'defaults': {'id': 'serial'},
                                     'checks': {'ck_classroom_school_verification_dates': 'contacted_at <= '
                                                                                          'verified_at AND '
                                                                                          'verified_at < '
                                                                                          'expires_at',
                                                'ck_classroom_school_method': 'verification_method IN '
                                                                              "('phone', 'official_email')",
                                                'ck_classroom_school_revocation': '(revoked_at IS NULL AND '
                                                                                  'revoked_by_id IS NULL AND '
                                                                                  'revocation_reason IS '
                                                                                  'NULL) OR (revoked_at IS '
                                                                                  'NOT NULL AND revoked_at '
                                                                                  '>= verified_at AND '
                                                                                  'revoked_by_id IS NOT NULL '
                                                                                  'AND revocation_reason IS '
                                                                                  'NOT NULL AND '
                                                                                  'revocation_reason IN '
                                                                                  "('withdrawn', "
                                                                                  "'authority_revoked', "
                                                                                  "'replaced'))",
                                                'ck_classroom_school_approval_date': 'approved_at IS NULL OR '
                                                                                     '(approved_at >= '
                                                                                     'verified_at AND '
                                                                                     'approved_at < '
                                                                                     'expires_at)',
                                                'ck_classroom_school_year': 'year_start BETWEEN 2000 AND '
                                                                            '9998',
                                                'ck_classroom_school_kind': "authority_kind IN ('director', "
                                                                            "'administrator')",
                                                'ck_classroom_school_independent': 'approver_id <> '
                                                                                   'verified_by_id'},
                                     'pg_checks': {'ck_classroom_school_approval_date': 'approved_at IS NULL '
                                                                                        'OR approved_at >= '
                                                                                        'verified_at AND '
                                                                                        'approved_at < '
                                                                                        'expires_at',
                                                   'ck_classroom_school_independent': 'approver_id <> '
                                                                                      'verified_by_id',
                                                   'ck_classroom_school_kind': 'authority_kind::text = ANY '
                                                                               "(ARRAY['director'::character "
                                                                               'varying, '
                                                                               "'administrator'::character "
                                                                               'varying]::text[])',
                                                   'ck_classroom_school_method': 'verification_method::text '
                                                                                 '= ANY '
                                                                                 "(ARRAY['phone'::character "
                                                                                 'varying, '
                                                                                 "'official_email'::character "
                                                                                 'varying]::text[])',
                                                   'ck_classroom_school_revocation': 'revoked_at IS NULL AND '
                                                                                     'revoked_by_id IS NULL '
                                                                                     'AND revocation_reason '
                                                                                     'IS NULL OR revoked_at '
                                                                                     'IS NOT NULL AND '
                                                                                     'revoked_at >= '
                                                                                     'verified_at AND '
                                                                                     'revoked_by_id IS NOT '
                                                                                     'NULL AND '
                                                                                     'revocation_reason IS '
                                                                                     'NOT NULL AND '
                                                                                     '(revocation_reason::text '
                                                                                     '= ANY '
                                                                                     "(ARRAY['withdrawn'::character "
                                                                                     'varying, '
                                                                                     "'authority_revoked'::character "
                                                                                     'varying, '
                                                                                     "'replaced'::character "
                                                                                     'varying]::text[]))',
                                                   'ck_classroom_school_verification_dates': 'contacted_at '
                                                                                             '<= verified_at '
                                                                                             'AND '
                                                                                             'verified_at < '
                                                                                             'expires_at',
                                                   'ck_classroom_school_year': 'year_start >= 2000 AND '
                                                                               'year_start <= 9998'},
                                     'foreign_keys': [(('approver_id',),
                                                       'trusted_verifiers',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('revoked_by_id',),
                                                       'trusted_verifiers',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('program_id',),
                                                       'classroom_programs',
                                                       ('organization_id',),
                                                       'RESTRICT'),
                                                      (('verified_by_id',),
                                                       'trusted_verifiers',
                                                       ('id',),
                                                       'RESTRICT')],
                                     'primary_key': ('id',),
                                     'unique': {},
                                     'indexes': {'uq_classroom_school_open_year': (('program_id',
                                                                                    'year_start'),
                                                                                   True,
                                                                                   'revoked_at IS NULL')}}}

CHART_C35 = {'classroom_chart_director_shares': {'exact': True,
                                     'columns': {'id': ('Integer', None, False),
                                                 'chart_id': ('Integer', None, False),
                                                 'class_id': ('Integer', None, False),
                                                 'assignment_id': ('Integer', None, False),
                                                 'membership_period_id': ('Integer', None, True),
                                                 'invitation_id': ('Integer', None, True),
                                                 'entitlement_id': ('Integer', None, False),
                                                 'account_declared_at': ('DateTime', None, False),
                                                 'class_activation_at': ('DateTime', None, False),
                                                 'source_audit_id': ('Integer', None, True),
                                                 'selected_at': ('DateTime', None, False),
                                                 'withdrawn_at': ('DateTime', None, True),
                                                 'guardian_permission_id': ('Integer', None, True)},
                                     'defaults': {'id': 'serial'},
                                     'checks': {'ck_classroom_chart_director_share_source': '(membership_period_id '
                                                                                            'IS NOT NULL AND '
                                                                                            'invitation_id '
                                                                                            'IS NULL) OR '
                                                                                            '(membership_period_id '
                                                                                            'IS NULL AND '
                                                                                            'invitation_id '
                                                                                            'IS NOT NULL)',
                                                'ck_classroom_chart_director_share_end': 'withdrawn_at IS '
                                                                                         'NULL OR '
                                                                                         'withdrawn_at >= '
                                                                                         'selected_at'},
                                     'pg_checks': {'ck_classroom_chart_director_share_source': 'membership_period_id '
                                                                                               'IS NOT NULL '
                                                                                               'AND '
                                                                                               'invitation_id '
                                                                                               'IS NULL OR '
                                                                                               'membership_period_id '
                                                                                               'IS NULL AND '
                                                                                               'invitation_id '
                                                                                               'IS NOT NULL',
                                                   'ck_classroom_chart_director_share_end': 'withdrawn_at IS '
                                                                                            'NULL OR '
                                                                                            'withdrawn_at >= '
                                                                                            'selected_at'},
                                     'foreign_keys': [(('invitation_id',),
                                                       'classroom_student_invitations',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('chart_id',), 'practice_charts', ('id',), 'CASCADE'),
                                                      (('class_id',),
                                                       'classroom_classes',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('assignment_id',),
                                                       'classroom_teaching_assignments',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('membership_period_id',),
                                                       'classroom_membership_periods',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('entitlement_id',),
                                                       'classroom_entitlements',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('source_audit_id',),
                                                       'classroom_s2_audit_events',
                                                       ('id',),
                                                       'RESTRICT'),
                                                      (('guardian_permission_id',),
                                                       'child_director_permissions',
                                                       ('id',),
                                                       'RESTRICT')],
                                     'primary_key': ('id',),
                                     'unique': {},
                                     'indexes': {'uq_classroom_chart_director_share_open': (('chart_id',
                                                                                             'assignment_id'),
                                                                                            True,
                                                                                            'withdrawn_at IS '
                                                                                            'NULL')}}}
