"""Frozen additive C34 guardian evidence schema; no school-authority issuer."""
C34 = {'classroom_guardian_authorizations': {'exact': True,
                                       'columns': {'id': ('Integer', None, False),
                                                   'program_id': ('Integer', None, False),
                                                   'class_id': ('Integer', None, False),
                                                   'profile_id': ('Integer', None, False),
                                                   'consent_id': ('Integer', None, False),
                                                   'verification_id': ('Integer', None, False),
                                                   'membership_period_id': ('Integer', None, False),
                                                   'entitlement_id': ('Integer', None, False),
                                                   'purpose': ('String', 16, False),
                                                   'participation_id': ('Integer', None, True),
                                                   'field_contract': ('String', 80, True),
                                                   'notice_version': ('String', 80, False),
                                                   'notice_digest': ('String', 64, False),
                                                   'attestation_version': ('String', 80, False),
                                                   'source_revision': ('String', 64, False),
                                                   'guardian_attested_at': ('DateTime', None, False),
                                                   'notice_accepted_at': ('DateTime', None, False),
                                                   'accepted_at': ('DateTime', None, False),
                                                   'expires_at': ('DateTime', None, False),
                                                   'withdrawn_at': ('DateTime', None, True),
                                                   'withdrawal_reason': ('String', 16, True)},
                                       'defaults': {'id': 'serial'},
                                       'checks': {'ck_classroom_guardian_field_contract': '(purpose = '
                                                                                          "'participation' "
                                                                                          'AND '
                                                                                          'field_contract IS '
                                                                                          'NULL AND '
                                                                                          'participation_id '
                                                                                          'IS NULL) OR '
                                                                                          '(purpose = '
                                                                                          "'reporting' AND "
                                                                                          'field_contract IS '
                                                                                          'NOT NULL AND '
                                                                                          'field_contract = '
                                                                                          "'classroom-reporting-41-v1' "
                                                                                          'AND '
                                                                                          'participation_id '
                                                                                          'IS NOT NULL)',
                                                  'ck_classroom_guardian_dates': 'expires_at > accepted_at '
                                                                                 'AND guardian_attested_at = '
                                                                                 'accepted_at AND '
                                                                                 'notice_accepted_at = '
                                                                                 'accepted_at',
                                                  'ck_classroom_guardian_purpose': 'purpose IN '
                                                                                   "('participation', "
                                                                                   "'reporting')",
                                                  'ck_classroom_guardian_withdrawal': '(withdrawn_at IS NULL '
                                                                                      'AND withdrawal_reason '
                                                                                      'IS NULL) OR '
                                                                                      '(withdrawn_at IS NOT '
                                                                                      'NULL AND '
                                                                                      'withdrawal_reason IS '
                                                                                      'NOT NULL AND '
                                                                                      'withdrawn_at >= '
                                                                                      'accepted_at AND '
                                                                                      'withdrawal_reason IN '
                                                                                      "('withdrawn', "
                                                                                      "'replaced'))"},
                                       'pg_checks': {'ck_classroom_guardian_dates': 'expires_at > '
                                                                                    'accepted_at AND '
                                                                                    'guardian_attested_at = '
                                                                                    'accepted_at AND '
                                                                                    'notice_accepted_at = '
                                                                                    'accepted_at',
                                                     'ck_classroom_guardian_field_contract': 'purpose::text '
                                                                                             '= '
                                                                                             "'participation'::text "
                                                                                             'AND '
                                                                                             'field_contract '
                                                                                             'IS NULL AND '
                                                                                             'participation_id '
                                                                                             'IS NULL OR '
                                                                                             'purpose::text '
                                                                                             '= '
                                                                                             "'reporting'::text "
                                                                                             'AND '
                                                                                             'field_contract '
                                                                                             'IS NOT NULL '
                                                                                             'AND '
                                                                                             'field_contract::text '
                                                                                             '= '
                                                                                             "'classroom-reporting-41-v1'::text "
                                                                                             'AND '
                                                                                             'participation_id '
                                                                                             'IS NOT NULL',
                                                     'ck_classroom_guardian_purpose': 'purpose::text = ANY '
                                                                                      "(ARRAY['participation'::character "
                                                                                      'varying, '
                                                                                      "'reporting'::character "
                                                                                      'varying]::text[])',
                                                     'ck_classroom_guardian_withdrawal': 'withdrawn_at IS '
                                                                                         'NULL AND '
                                                                                         'withdrawal_reason '
                                                                                         'IS NULL OR '
                                                                                         'withdrawn_at IS '
                                                                                         'NOT NULL AND '
                                                                                         'withdrawal_reason '
                                                                                         'IS NOT NULL AND '
                                                                                         'withdrawn_at >= '
                                                                                         'accepted_at AND '
                                                                                         '(withdrawal_reason::text '
                                                                                         '= ANY '
                                                                                         "(ARRAY['withdrawn'::character "
                                                                                         'varying, '
                                                                                         "'replaced'::character "
                                                                                         'varying]::text[]))'},
                                       'primary_key': ('id',),
                                       'unique': {'uq_classroom_guardian_scope': ('id',
                                                                                  'program_id',
                                                                                  'class_id',
                                                                                  'profile_id')},
                                       'indexes': {'uq_classroom_guardian_open': (('program_id',
                                                                                   'class_id',
                                                                                   'profile_id',
                                                                                   'purpose'),
                                                                                  True,
                                                                                  'withdrawn_at IS NULL')},
                                       'foreign_keys': [(('participation_id',
                                                          'program_id',
                                                          'class_id',
                                                          'profile_id'),
                                                         'classroom_guardian_authorizations',
                                                         ('id', 'program_id', 'class_id', 'profile_id'),
                                                         'RESTRICT'),
                                                        (('verification_id',),
                                                         'child_kws_verifications',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('profile_id',),
                                                         'woodchuck_profiles',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('membership_period_id',),
                                                         'classroom_membership_periods',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('consent_id',),
                                                         'child_consent_evidence',
                                                         ('id',),
                                                         'RESTRICT'),
                                                        (('class_id', 'program_id'),
                                                         'classroom_classes',
                                                         ('id', 'program_id'),
                                                         'RESTRICT'),
                                                        (('entitlement_id',),
                                                         'classroom_entitlements',
                                                         ('id',),
                                                         'RESTRICT')]},
 'classroom_guardian_recipients': {'exact': True,
                                   'columns': {'authorization_id': ('Integer', None, False),
                                               'assignment_id': ('Integer', None, False),
                                               'program_id': ('Integer', None, False),
                                               'class_id': ('Integer', None, False),
                                               'profile_id': ('Integer', None, False)},
                                   'defaults': {},
                                   'checks': {},
                                   'pg_checks': {},
                                   'primary_key': ('authorization_id', 'assignment_id'),
                                   'unique': {},
                                   'indexes': {},
                                   'foreign_keys': [(('assignment_id', 'class_id', 'program_id'),
                                                     'classroom_teaching_assignments',
                                                     ('id', 'class_id', 'program_id'),
                                                     'RESTRICT'),
                                                    (('authorization_id',
                                                      'program_id',
                                                      'class_id',
                                                      'profile_id'),
                                                     'classroom_guardian_authorizations',
                                                     ('id', 'program_id', 'class_id', 'profile_id'),
                                                     'RESTRICT')]},
 'classroom_guardian_audits': {'exact': True,
                               'columns': {'id': ('Integer', None, False),
                                           'authorization_id': ('Integer', None, False),
                                           'program_id': ('Integer', None, False),
                                           'class_id': ('Integer', None, False),
                                           'profile_id': ('Integer', None, False),
                                           'actor_consent_id': ('Integer', None, False),
                                           'action': ('String', 16, False),
                                           'occurred_at': ('DateTime', None, False)},
                               'defaults': {'id': 'serial'},
                               'checks': {'ck_classroom_guardian_audit_action': "action IN ('accepted', "
                                                                                "'withdrawn', 'replaced')"},
                               'pg_checks': {'ck_classroom_guardian_audit_action': 'action::text = ANY '
                                                                                   "(ARRAY['accepted'::character "
                                                                                   'varying, '
                                                                                   "'withdrawn'::character "
                                                                                   'varying, '
                                                                                   "'replaced'::character "
                                                                                   'varying]::text[])'},
                               'primary_key': ('id',),
                               'unique': {'uq_classroom_guardian_audit_action': ('authorization_id',
                                                                                 'action')},
                               'indexes': {},
                               'foreign_keys': [(('actor_consent_id',),
                                                 'child_consent_evidence',
                                                 ('id',),
                                                 'RESTRICT'),
                                                (('authorization_id', 'program_id', 'class_id', 'profile_id'),
                                                 'classroom_guardian_authorizations',
                                                 ('id', 'program_id', 'class_id', 'profile_id'),
                                                 'RESTRICT')]}}
