"""Frozen C33 schema contract, independent of live ORM and feature gates."""
IDENTITY_CHECKS = {
    'ck_classroom_identity_name': 'length(trim(school_name)) BETWEEN 1 AND 150 AND school_name = trim(school_name)',
    'ck_classroom_identity_version': 'version >= 1',
}
AUDIT_CHECKS = {
    'ck_classroom_identity_audit_version': 'version >= 1',
    'ck_classroom_identity_audit_action': "(action = 'created' AND version = 1) OR (action = 'corrected' AND version > 1)",
}

C33 = {
    'classroom_roster_identities': {
        'exact': True,
        'columns': {'id': ('Integer', None, False), 'program_id': ('Integer', None, False),
            'profile_id': ('Integer', None, False), 'school_name': ('String', 150, False),
            'version': ('Integer', None, False), 'updated_at': ('DateTime', None, False)},
        'defaults': {'id': 'serial'}, 'checks': IDENTITY_CHECKS, 'pg_checks': IDENTITY_CHECKS,
        'primary_key': ('id',),
        'unique': {'uq_classroom_identity_program_profile': ('program_id', 'profile_id'),
            'uq_classroom_identity_scope': ('id', 'program_id', 'profile_id')}, 'indexes': {},
        'foreign_keys': [(('program_id',), 'classroom_programs', ('organization_id',), 'RESTRICT'),
            (('profile_id',), 'woodchuck_profiles', ('id',), 'RESTRICT')],
    },
    'classroom_roster_identity_audits': {
        'exact': True,
        'columns': {'id': ('Integer', None, False), 'identity_id': ('Integer', None, False),
            'program_id': ('Integer', None, False), 'profile_id': ('Integer', None, False),
            'class_id': ('Integer', None, False), 'actor_verifier_id': ('Integer', None, False),
            'version': ('Integer', None, False), 'action': ('String', 10, False),
            'occurred_at': ('DateTime', None, False)},
        'defaults': {'id': 'serial'}, 'checks': AUDIT_CHECKS, 'pg_checks': AUDIT_CHECKS,
        'primary_key': ('id',), 'unique': {'uq_classroom_identity_audit_version': ('identity_id', 'version')},
        'indexes': {}, 'foreign_keys': [
            (('identity_id', 'program_id', 'profile_id'), 'classroom_roster_identities', ('id', 'program_id', 'profile_id'), 'RESTRICT'),
            (('class_id', 'program_id'), 'classroom_classes', ('id', 'program_id'), 'RESTRICT'),
            (('actor_verifier_id',), 'trusted_verifiers', ('id',), 'RESTRICT')],
    },
}

# Exact PostgreSQL 18 reflection forms from the actual additive migration.
C33['classroom_roster_identities']['pg_checks'] = {
    'ck_classroom_identity_name': 'length(TRIM(BOTH FROM school_name)) >= 1 AND length(TRIM(BOTH FROM school_name)) <= 150 AND school_name::text = TRIM(BOTH FROM school_name)',
    'ck_classroom_identity_version': 'version >= 1',
}
C33['classroom_roster_identity_audits']['pg_checks'] = {
    'ck_classroom_identity_audit_action': "action::text = 'created'::text AND version = 1 OR action::text = 'corrected'::text AND version > 1",
    'ck_classroom_identity_audit_version': 'version >= 1',
}
