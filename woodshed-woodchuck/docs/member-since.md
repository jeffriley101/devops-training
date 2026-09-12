# Member Since timestamp

SHED displays `woodchuck_profiles.created_at` as the member start date. This is
the authoritative server-side timestamp written when a persistent Woodchuck
account/profile is created. The UI formats a copy for display and does not
modify the stored value or source the date from browser storage.

No schema migration or backfill is currently necessary because
`woodchuck_profiles.created_at` has been non-null since persistent accounts
were introduced.

If a future imported or legacy record lacks that value, its backfill order is:

1. an earlier persisted server-side account creation timestamp, if an account
   table is introduced;
2. the profile creation timestamp;
3. the earliest timestamp on another persisted server-side record belonging
   to that profile (including a P-Chart or contest record only as a last
   resort).

A missing date remains null until one of those sources exists. Deployment or
migration time must never be substituted.
