# Seasonal Team activation retired

Seasons organize presentation, calendar labels, and historical results. They do
not create successor Teams, copy memberships, reset identity, or reset lifetime
totals. ContestWeek governs weekly scoring, rankings, medals, and correction
allowance. Membership changes only when a student leaves or changes Teams.

The normal `team_activate` command now returns `NOT_READY` with
`seasonal_team_activation_retired` without opening a database connection. Do not
schedule it or use it to prepare Halloween Teams. Calendar provisioning and
ordinary weekly finalization remain separate operations.

Use the [persistent Team authority runbook](persistent-team-cutover.md) for the
additive schema and separately reviewed Option B cutover. Deployment does not
activate authority. The approved existing Team and membership IDs must pass a
fresh read-only preflight; application uses explicit authority markers after the
operator's atomic transition. No name-based or latest-season selection occurs.

The old continuity engine and guarded repair tooling remain historical
maintenance facilities. Their revision, maintenance, ambiguity, and historical
preservation guards are unchanged. They are not normal future season operations.
Read-only historical inventory can be used where its schema guard permits it;
no diagnostic result authorizes copying Teams or memberships.

Historical run instructions are available in Git history. They are superseded
for future operations by persistent identity and versioned ContestWeeks. The
current September 28, 2026 week keeps legacy scoring rules; persistent weekly
readers begin only at an explicitly approved clean later Monday. Stored results,
snapshots, report state, earning-time attribution, and existing NULL attribution
remain unchanged.
