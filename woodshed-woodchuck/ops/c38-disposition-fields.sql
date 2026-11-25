-- Apply to the isolated PostgreSQL custodian after C38.
-- Ordinary web and Support identities receive none of these grants.
BEGIN;
GRANT DELETE ON public.classroom_student_memberships, public.classroom_membership_holds,
    public.classroom_reporting_periods, public.classroom_membership_periods,
    public.classroom_teaching_assignments TO disposition_custodian;
GRANT SELECT, DELETE ON public.classroom_entry_codes,
    public.classroom_entitlements TO disposition_custodian;
GRANT UPDATE (actor_verifier_id, target_verifier_id, assignment_id, role_grant_id, new_owner_id)
    ON public.classroom_audit_events TO disposition_custodian;
GRANT UPDATE (actor_verifier_id, actor_profile_id, actor_admin, entitlement_id, code_id, profile_id, membership_id)
    ON public.classroom_s2_audit_events TO disposition_custodian;
GRANT UPDATE (changed_by_verifier_id) ON public.classroom_class_states TO disposition_custodian;
GRANT EXECUTE ON FUNCTION public.c38_evidence_scope(text, text, jsonb, jsonb),
    public.c38_require_evidence_witness(text, text, jsonb, jsonb) TO disposition_custodian;
GRANT EXECUTE ON FUNCTION public.c38_evidence_scope(text, text, jsonb, jsonb),
    public.c38_require_lifecycle_witness(text, text, jsonb, jsonb) TO disposition_ordinary;
COMMIT;
