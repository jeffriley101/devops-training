"""Frozen c32 enrollment storage contract, independent of live ORM and gates."""
INVITATION_CHECKS = {
    'ck_classroom_enrollment_purpose': "purpose = 'classroom-enrollment-v2'",
    'ck_classroom_enrollment_state': "state IN ('pending', 'deferred', 'accepted', 'declined', 'expired', 'revoked')",
    'ck_classroom_enrollment_expiry': 'expires_at > issued_at',
    'ck_classroom_enrollment_resolution': "(state IN ('pending', 'deferred') AND resolved_at IS NULL AND accepted_period_id IS NULL) OR (state IN ('accepted', 'declined', 'expired', 'revoked') AND resolved_at IS NOT NULL AND resolved_at >= issued_at)",
    'ck_classroom_enrollment_acceptance': "(state = 'accepted' AND accepted_period_id IS NOT NULL) OR (state <> 'accepted' AND accepted_period_id IS NULL)",
    'ck_classroom_enrollment_response_time': "state NOT IN ('accepted', 'declined') OR resolved_at < expires_at",
    'ck_classroom_enrollment_expired_time': "state <> 'expired' OR resolved_at >= expires_at",
}
AUDIT_CHECKS = {
    'ck_classroom_enrollment_audit_action': "action IN ('issued', 'accepted', 'declined', 'deferred', 'expired', 'revoked')",
    'ck_classroom_enrollment_audit_actor': "(actor_profile_id IS NOT NULL AND actor_profile_id = profile_id AND actor_verifier_id IS NULL AND action IN ('accepted', 'declined', 'deferred', 'expired')) OR (actor_verifier_id IS NOT NULL AND actor_profile_id IS NULL AND action IN ('issued', 'revoked', 'expired'))",
    'ck_classroom_enrollment_audit_period': "(action = 'accepted' AND membership_period_id IS NOT NULL) OR (action <> 'accepted' AND membership_period_id IS NULL)",
}

C32 = {
    'classroom_enrollment_invitations': {
        'exact': True,
        'columns': {
            'id': ('Integer', None, False), 'purpose': ('String', 32, False),
            'program_id': ('Integer', None, False), 'class_id': ('Integer', None, False),
            'assignment_id': ('Integer', None, False), 'profile_id': ('Integer', None, False),
            'issued_by_verifier_id': ('Integer', None, False), 'issued_at': ('DateTime', None, False),
            'expires_at': ('DateTime', None, False), 'state': ('String', 10, False),
            'resolved_at': ('DateTime', None, True), 'accepted_period_id': ('Integer', None, True),
        },
        'defaults': {'id': 'serial'}, 'checks': INVITATION_CHECKS,
        'pg_checks': INVITATION_CHECKS,
        'primary_key': ('id',),
        'unique': {'uq_classroom_enrollment_invitation_scope': ('id', 'program_id', 'class_id', 'profile_id')},
        'indexes': {
            'uq_classroom_enrollment_invitation_open': (('assignment_id', 'profile_id'), True, 'resolved_at IS NULL'),
            'ix_classroom_enrollment_student_state': (('profile_id', 'state'), False, None),
        },
        'foreign_keys': [
            (('program_id',), 'classroom_programs', ('organization_id',), 'RESTRICT'),
            (('class_id', 'program_id'), 'classroom_classes', ('id', 'program_id'), 'RESTRICT'),
            (('assignment_id', 'class_id', 'program_id'), 'classroom_teaching_assignments', ('id', 'class_id', 'program_id'), 'RESTRICT'),
            (('profile_id',), 'woodchuck_profiles', ('id',), 'RESTRICT'),
            (('issued_by_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('accepted_period_id',), 'classroom_membership_periods', ('id',), 'RESTRICT'),
        ],
    },
    'classroom_enrollment_audit_events': {
        'exact': True,
        'columns': {
            'id': ('Integer', None, False), 'invitation_id': ('Integer', None, False),
            'program_id': ('Integer', None, False), 'class_id': ('Integer', None, False),
            'profile_id': ('Integer', None, False), 'action': ('String', 10, False),
            'actor_profile_id': ('Integer', None, True), 'actor_verifier_id': ('Integer', None, True),
            'membership_period_id': ('Integer', None, True), 'occurred_at': ('DateTime', None, False),
        },
        'defaults': {'id': 'serial'}, 'checks': AUDIT_CHECKS, 'pg_checks': AUDIT_CHECKS,
        'primary_key': ('id',), 'unique': {'uq_classroom_enrollment_audit_action': ('invitation_id', 'action')}, 'indexes': {},
        'foreign_keys': [
            (('invitation_id', 'program_id', 'class_id', 'profile_id'), 'classroom_enrollment_invitations', ('id', 'program_id', 'class_id', 'profile_id'), 'RESTRICT'),
            (('actor_profile_id',), 'woodchuck_profiles', ('id',), 'RESTRICT'),
            (('actor_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT'),
            (('membership_period_id',), 'classroom_membership_periods', ('id',), 'RESTRICT'),
        ],
    },
}

# Frozen PostgreSQL 18 native representations from the actual additive migration.
C32['classroom_enrollment_invitations']['pg_checks'] = {'ck_classroom_enrollment_acceptance': "state::text = 'accepted'::text AND accepted_period_id IS NOT NULL OR "
                                       "state::text <> 'accepted'::text AND accepted_period_id IS NULL",
 'ck_classroom_enrollment_expired_time': "state::text <> 'expired'::text OR resolved_at >= expires_at",
 'ck_classroom_enrollment_expiry': 'expires_at > issued_at',
 'ck_classroom_enrollment_purpose': "purpose::text = 'classroom-enrollment-v2'::text",
 'ck_classroom_enrollment_resolution': "(state::text = ANY (ARRAY['pending'::character varying, "
                                       "'deferred'::character varying]::text[])) AND resolved_at IS NULL AND "
                                       'accepted_period_id IS NULL OR (state::text = ANY '
                                       "(ARRAY['accepted'::character varying, 'declined'::character varying, "
                                       "'expired'::character varying, 'revoked'::character "
                                       'varying]::text[])) AND resolved_at IS NOT NULL AND resolved_at >= '
                                       'issued_at',
 'ck_classroom_enrollment_response_time': "(state::text <> ALL (ARRAY['accepted'::character varying, "
                                          "'declined'::character varying]::text[])) OR resolved_at < "
                                          'expires_at',
 'ck_classroom_enrollment_state': "state::text = ANY (ARRAY['pending'::character varying, "
                                  "'deferred'::character varying, 'accepted'::character varying, "
                                  "'declined'::character varying, 'expired'::character varying, "
                                  "'revoked'::character varying]::text[])"}
C32['classroom_enrollment_audit_events']['pg_checks'] = {'ck_classroom_enrollment_audit_action': "action::text = ANY (ARRAY['issued'::character varying, "
                                         "'accepted'::character varying, 'declined'::character varying, "
                                         "'deferred'::character varying, 'expired'::character varying, "
                                         "'revoked'::character varying]::text[])",
 'ck_classroom_enrollment_audit_actor': 'actor_profile_id IS NOT NULL AND actor_profile_id = profile_id AND '
                                        'actor_verifier_id IS NULL AND (action::text = ANY '
                                        "(ARRAY['accepted'::character varying, 'declined'::character "
                                        "varying, 'deferred'::character varying, 'expired'::character "
                                        'varying]::text[])) OR actor_verifier_id IS NOT NULL AND '
                                        'actor_profile_id IS NULL AND (action::text = ANY '
                                        "(ARRAY['issued'::character varying, 'revoked'::character varying, "
                                        "'expired'::character varying]::text[]))",
 'ck_classroom_enrollment_audit_period': "action::text = 'accepted'::text AND membership_period_id IS NOT "
                                         "NULL OR action::text <> 'accepted'::text AND membership_period_id "
                                         'IS NULL'}

# Exact alternate spellings observed after PostgreSQL 18 native dump/restore.
# Unsized varchar-to-text casts only; no general cast, literal or grouping rewrite.
POSTGRES_RESTORE_CHECK_FORMS = {('classroom_enrollment_audit_events', 'ck_classroom_enrollment_audit_action'): 'action::text = ANY '
                                                                                "(ARRAY['issued'::character "
                                                                                'varying::text, '
                                                                                "'accepted'::character "
                                                                                'varying::text, '
                                                                                "'declined'::character "
                                                                                'varying::text, '
                                                                                "'deferred'::character "
                                                                                'varying::text, '
                                                                                "'expired'::character "
                                                                                'varying::text, '
                                                                                "'revoked'::character "
                                                                                'varying::text])',
 ('classroom_enrollment_audit_events', 'ck_classroom_enrollment_audit_actor'): 'actor_profile_id IS NOT NULL '
                                                                               'AND actor_profile_id = '
                                                                               'profile_id AND '
                                                                               'actor_verifier_id IS NULL '
                                                                               'AND (action::text = ANY '
                                                                               "(ARRAY['accepted'::character "
                                                                               'varying::text, '
                                                                               "'declined'::character "
                                                                               'varying::text, '
                                                                               "'deferred'::character "
                                                                               'varying::text, '
                                                                               "'expired'::character "
                                                                               'varying::text])) OR '
                                                                               'actor_verifier_id IS NOT '
                                                                               'NULL AND actor_profile_id IS '
                                                                               'NULL AND (action::text = ANY '
                                                                               "(ARRAY['issued'::character "
                                                                               'varying::text, '
                                                                               "'revoked'::character "
                                                                               'varying::text, '
                                                                               "'expired'::character "
                                                                               'varying::text]))',
 ('classroom_enrollment_invitations', 'ck_classroom_enrollment_resolution'): '(state::text = ANY '
                                                                             "(ARRAY['pending'::character "
                                                                             'varying::text, '
                                                                             "'deferred'::character "
                                                                             'varying::text])) AND '
                                                                             'resolved_at IS NULL AND '
                                                                             'accepted_period_id IS NULL OR '
                                                                             '(state::text = ANY '
                                                                             "(ARRAY['accepted'::character "
                                                                             'varying::text, '
                                                                             "'declined'::character "
                                                                             'varying::text, '
                                                                             "'expired'::character "
                                                                             'varying::text, '
                                                                             "'revoked'::character "
                                                                             'varying::text])) AND '
                                                                             'resolved_at IS NOT NULL AND '
                                                                             'resolved_at >= issued_at',
 ('classroom_enrollment_invitations', 'ck_classroom_enrollment_response_time'): '(state::text <> ALL '
                                                                                "(ARRAY['accepted'::character "
                                                                                'varying::text, '
                                                                                "'declined'::character "
                                                                                'varying::text])) OR '
                                                                                'resolved_at < expires_at',
 ('classroom_enrollment_invitations', 'ck_classroom_enrollment_state'): 'state::text = ANY '
                                                                        "(ARRAY['pending'::character "
                                                                        'varying::text, '
                                                                        "'deferred'::character "
                                                                        'varying::text, '
                                                                        "'accepted'::character "
                                                                        'varying::text, '
                                                                        "'declined'::character "
                                                                        "varying::text, 'expired'::character "
                                                                        "varying::text, 'revoked'::character "
                                                                        'varying::text])'}
