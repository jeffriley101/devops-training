# Credited practice duration

`app/practice_duration.py` defines credited seconds: valid Pristine recorded
seconds (including an explicit zero) take precedence; otherwise stored minutes
supply the duration. Ordinary charts never add detected seconds. SQL totals use
the equivalent expression, covered against the same boundary cases.

Totals add seconds before converting to minutes or formatting. Two 59-second
charts now contribute **1 minute 58 seconds**, rather than zero minutes. An
ordinary two-minute chart for the same practice intentionally adds another two
minutes of credit; the total is **3 minutes 58 seconds of credited practice**,
not unique elapsed time. Currency and rating bonuses are separate.

Weekly/career totals, Insights and director summaries expose exact seconds as
well as compatible minute fields, which can now be fractional. Director CSV
keeps existing minute columns and adds explicitly labeled weekly/career seconds
columns for lossless duration export. Book totals and positive-day counts use
saved charts; exports label browser-local legacy entries separately. Displayed
averages round to the nearest second only at presentation.

## Scoring and history

Unfinalized duration standings now compare full totals/averages, so a one-second
difference can break a former whole-minute tie. Equal durations retain the
existing Olympic ranks and name ordering. Average comparisons can also change
for minute-only charts whose averages previously rounded to the same score. Five-minute activity thresholds,
member caps, dandelion rates, rating bonuses and TPR's one-decimal score formula
are unchanged. TPR accepts fractional minute input. Practice XP retains the
existing one-XP-per-minute rate with fractional minutes; displayed XP rounds to
two decimals, while level comparisons use the unrounded value.

The existing `ContestResult.score` column is integer-only. Migration
`t0p1q2r3s4t5` therefore adds nullable `precise_score` (practice minutes) for new
snapshots while retaining the integer compatibility field. It also adds
`ContestWeek.practice_scoring_mode`. At this pre-precision migration boundary,
already-finalized weeks are marked `legacy_minutes`; open/pending weeks remain
unmarked. This annotates provenance without changing frozen results. Readers prefer the
precise value when present. **No historical scores are backfilled, and no
awards, rewards, membership snapshots or crowns are recomputed.** Explicit
legacy-history repairs retain the old minute inputs and rounded team averages.
New finalizations atomically record `precise_seconds` with their results. Their
repairs retain seconds and unrounded averages, even when result rows are
missing. New legacy repair rows keep `precise_score` NULL. A missing/unknown
week marker, snapshots contradicting that marker, or a missing original cutoff
causes a 409 refusal before repair artifacts are created. Neither a finalized
status nor surviving result rows (even all-precise rows) substitute for the
week-level attestation. Normal repeated finalization remains a no-op.
Director results already support fractional scores.

## Release ordering and maintenance compatibility

1. Back up the database. Pause and drain all finalization writers, including
   scheduled/CLI finalizers and web/admin repair or finalization requests.
2. Apply migration `t0p1q2r3s4t5` **before starting either the new web code or
   the new finalizer code**. Both map the new columns. This migration is still
   part of the unreleased precision change; recreate previously migrated
   disposable development databases to test the amended migration.
3. Update both services and every finalization entry point together, then
   resume finalization. Do not allow old/new finalizers to overlap: locks
   serialize writes but cannot reconcile different scoring rules. An old
   finalizer running after migration leaves a newly finalized week unmarked;
   repair will refuse it rather than guess. Mixed snapshot modes are refused.

Rollback is not lossless: **dropping `precise_score` permanently discards the
recorded fractional scores**, and dropping `practice_scoring_mode` discards
scoring provenance. Integer compatibility values and precise ranks cannot
reconstruct the lost fractions. Preserve a restorable backup before downgrade;
do not treat downgrade/re-upgrade as a safe historical repair strategy. An
old-code rollback with the additive schema retained still requires finalizers
and repairs to stay paused: old code does not honor precise scoring markers.
Resolve service/schema compatibility and preserve or restore provenance before
resuming historical repairs.

Compatibility inspection: `app/team_continuity_repair.py::schema_guard` pins
`REVISION` to `s9n0o1p2q3r4` and rejects every other head. Current season
activation and continuity maintenance use it. The protected
`feature/contest-week-provisioning` branch at `3196ee7` has the same guard and
calls it for both provisioning plan and apply. It **does not accept
`t0p1q2r3s4t5`**; the check returns `revision_not_approved`. No guard was weakened
or protected branch merged. Using these maintenance tools after the precision
migration requires a separate reviewed compatibility update (including
preservation/snapshot coverage of the new fields) and coordinated release.
Do not bypass the revision check or rewrite `alembic_version`.

## Limits

Ordinary timer rounding and whole-minute chart entry are unchanged. Missing
historical seconds cannot be recovered. Current submissions still reject
zero-second Pristine charts; the read helper distinguishes zero from missing
for legacy/imported data. The separate concurrent same-key retry issue remains
untouched. Validation uses disposable databases only.
