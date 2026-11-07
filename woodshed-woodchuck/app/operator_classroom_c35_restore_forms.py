"""Exact C34/C35 PostgreSQL CHECK pairs from a native migration and dump/restore.

These six pairs only distribute varchar[] -> text[] casts over literal array
elements. No predicates, values, casts, or Boolean grouping are discarded.
Keep the C5/C32 mappings, frozen schema contracts, and SQL normalizer unchanged.
"""

POSTGRES_RESTORE_CHECK_FORMS = {
    ('classroom_guardian_authorizations', 'ck_classroom_guardian_purpose'): (
        "purpose::text = ANY (ARRAY['participation'::character varying, 'reporting'::character varying]::text[])",
        "purpose::text = ANY (ARRAY['participation'::character varying::text, 'reporting'::character varying::text])",
    ),
    ('classroom_guardian_authorizations', 'ck_classroom_guardian_withdrawal'): (
        "withdrawn_at IS NULL AND withdrawal_reason IS NULL OR withdrawn_at IS NOT NULL AND withdrawal_reason IS NOT NULL AND withdrawn_at >= accepted_at AND (withdrawal_reason::text = ANY (ARRAY['withdrawn'::character varying, 'replaced'::character varying]::text[]))",
        "withdrawn_at IS NULL AND withdrawal_reason IS NULL OR withdrawn_at IS NOT NULL AND withdrawal_reason IS NOT NULL AND withdrawn_at >= accepted_at AND (withdrawal_reason::text = ANY (ARRAY['withdrawn'::character varying::text, 'replaced'::character varying::text]))",
    ),
    ('classroom_guardian_audits', 'ck_classroom_guardian_audit_action'): (
        "action::text = ANY (ARRAY['accepted'::character varying, 'withdrawn'::character varying, 'replaced'::character varying]::text[])",
        "action::text = ANY (ARRAY['accepted'::character varying::text, 'withdrawn'::character varying::text, 'replaced'::character varying::text])",
    ),
    ('classroom_school_authorizations', 'ck_classroom_school_kind'): (
        "authority_kind::text = ANY (ARRAY['director'::character varying, 'administrator'::character varying]::text[])",
        "authority_kind::text = ANY (ARRAY['director'::character varying::text, 'administrator'::character varying::text])",
    ),
    ('classroom_school_authorizations', 'ck_classroom_school_method'): (
        "verification_method::text = ANY (ARRAY['phone'::character varying, 'official_email'::character varying]::text[])",
        "verification_method::text = ANY (ARRAY['phone'::character varying::text, 'official_email'::character varying::text])",
    ),
    ('classroom_school_authorizations', 'ck_classroom_school_revocation'): (
        "revoked_at IS NULL AND revoked_by_id IS NULL AND revocation_reason IS NULL OR revoked_at IS NOT NULL AND revoked_at >= verified_at AND revoked_by_id IS NOT NULL AND revocation_reason IS NOT NULL AND (revocation_reason::text = ANY (ARRAY['withdrawn'::character varying, 'authority_revoked'::character varying, 'replaced'::character varying]::text[]))",
        "revoked_at IS NULL AND revoked_by_id IS NULL AND revocation_reason IS NULL OR revoked_at IS NOT NULL AND revoked_at >= verified_at AND revoked_by_id IS NOT NULL AND revocation_reason IS NOT NULL AND (revocation_reason::text = ANY (ARRAY['withdrawn'::character varying::text, 'authority_revoked'::character varying::text, 'replaced'::character varying::text]))",
    ),
}
