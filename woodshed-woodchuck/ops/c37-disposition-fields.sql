-- Isolated custodian only. Its OS identity and credential belong exclusively
-- to the signed-case executor. No object grant option or role granted to the executor.
BEGIN;
GRANT SELECT, DELETE ON public.classroom_roster_identities,
    public.classroom_roster_identity_audits, public.classroom_enrollment_invitations,
    public.classroom_enrollment_audit_events, public.classroom_student_invitations,
    public.classroom_chart_director_shares, public.classroom_progress_authorizations,
    public.classroom_progress_recipients TO disposition_custodian;
GRANT SELECT ON public.classroom_membership_periods, public.classroom_teaching_assignments,
    public.classroom_s2_audit_events TO disposition_custodian;
GRANT DELETE ON public.classroom_s2_audit_events TO disposition_custodian;
GRANT SELECT ON public.classroom_class_states TO disposition_custodian;
GRANT SELECT(id,profile_id) ON public.practice_charts TO disposition_custodian;
GRANT SELECT, DELETE ON public.classroom_s3_audit_events, public.classroom_audit_events,
    public.classroom_role_grants TO disposition_custodian;
GRANT SELECT ON public.classroom_reporting_periods TO disposition_custodian;
GRANT SELECT(id,is_current) ON public.classroom_entry_codes TO disposition_custodian;
COMMIT;
