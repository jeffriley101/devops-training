-- Disposable qualification only. The proposed privileges fail the other-account boundary.
-- psql role placeholders refer to the two fixture logins. Direct execution rolls back.
BEGIN;

-- Student account cleanup by the guardian Support executor, including prior deletions.
GRANT SELECT (profile_id) ON TABLE public.account_age_corrections TO :"guardian_role";
GRANT SELECT (profile_id, consent_id, private_plunge_best) ON TABLE public.account_privacy_rules TO :"guardian_role";
GRANT SELECT (id, profile_id) ON TABLE public.arcade_attempt_packs TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.arcade_high_scores TO :"guardian_role";
GRANT SELECT (id, profile_id, pack_id) ON TABLE public.arcade_play_sessions TO :"guardian_role";
GRANT SELECT (profile_id, play_id) ON TABLE public.arcade_start_requests TO :"guardian_role";
GRANT SELECT (profile_id, duplicate_key) ON TABLE public.camp_point_awards TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.child_parent_age_declarations TO :"guardian_role";
GRANT SELECT (chart_id) ON TABLE public.classroom_chart_director_shares TO :"guardian_role";
GRANT SELECT (chart_id) ON TABLE public.classroom_chart_shares TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.classroom_enrollment_invitations TO :"guardian_role";
GRANT SELECT (profile_id, purpose, accepted_at, expires_at, withdrawn_at) ON TABLE public.classroom_guardian_authorizations TO :"guardian_role";
GRANT SELECT (profile_id, accepted_at, withdrawn_at) ON TABLE public.classroom_progress_authorizations TO :"guardian_role";
GRANT SELECT (profile_id, starts_at, ended_at) ON TABLE public.classroom_reporting_periods TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.classroom_student_invitations TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.classroom_student_memberships TO :"guardian_role";
GRANT SELECT (id, profile_id) ON TABLE public.crown_awards TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.daily_trivia_attempts TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.owned_item_copies TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.plunge_point_awards TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.quest_completions TO :"guardian_role";
GRANT SELECT (id, profile_id, source_key) ON TABLE public.reward_grants TO :"guardian_role";
GRANT SELECT (profile_id, crown_award_id, reward_grant_id) ON TABLE public.reward_inventory_placements TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.team_week_membership_snapshots TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.traveling_cup_placements TO :"guardian_role";
GRANT SELECT (profile_id) ON TABLE public.weekly_newsletter_presentations TO :"guardian_role";
GRANT SELECT (profile_id, issued_at) ON TABLE public.weekly_plans TO :"guardian_role";
GRANT SELECT (id, profile_id) ON TABLE public.weekly_wheel_plays TO :"guardian_role";

GRANT UPDATE (private_plunge_best) ON TABLE public.account_privacy_rules TO :"guardian_role";
GRANT UPDATE (last_email_attempt_at, last_email_sent_at, email_attempt_count) ON TABLE public.practice_chart_verifications TO :"guardian_role";
GRANT UPDATE (ordinary_email_attempted_at, ordinary_email_sent_at) ON TABLE public.practice_charts TO :"guardian_role";
GRANT UPDATE (last_email_attempt_at, last_email_sent_at, email_attempt_count) ON TABLE public.trusted_verifier_invitations TO :"guardian_role";
GRANT UPDATE (plunge_best_score) ON TABLE public.woodchuck_profiles TO :"guardian_role";

GRANT DELETE ON TABLE public.account_privacy_rules TO :"guardian_role";
GRANT DELETE ON TABLE public.arcade_attempt_packs TO :"guardian_role";
GRANT DELETE ON TABLE public.arcade_high_scores TO :"guardian_role";
GRANT DELETE ON TABLE public.arcade_play_sessions TO :"guardian_role";
GRANT DELETE ON TABLE public.arcade_start_requests TO :"guardian_role";
GRANT DELETE ON TABLE public.daily_trivia_attempts TO :"guardian_role";
GRANT DELETE ON TABLE public.login_streaks TO :"guardian_role";
GRANT DELETE ON TABLE public.owned_item_copies TO :"guardian_role";
GRANT DELETE ON TABLE public.plunge_point_awards TO :"guardian_role";
GRANT DELETE ON TABLE public.practice_charts TO :"guardian_role";
GRANT DELETE ON TABLE public.quest_completions TO :"guardian_role";
GRANT DELETE ON TABLE public.reward_inventory_placements TO :"guardian_role";
GRANT DELETE ON TABLE public.traveling_cup_placements TO :"guardian_role";
GRANT DELETE ON TABLE public.weekly_newsletter_presentations TO :"guardian_role";
GRANT DELETE ON TABLE public.weekly_plans TO :"guardian_role";
GRANT DELETE ON TABLE public.weekly_wheel_plays TO :"guardian_role";

-- Verified adult account cleanup, including prior deletions.
GRANT UPDATE (email) ON TABLE public.trusted_verifier_invitations TO :"adult_role";

ROLLBACK;
