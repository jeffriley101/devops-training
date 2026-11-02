# Student Classroom home — local implementation

Starting checkpoint: `c3a3299`. `/classroom` uses the existing S2 and Enrollment
V2 gates; their defaults and all activation settings are unchanged. No migration
is needed. My Woodshed and enrollment navigation link to the student home.

The page authenticates the native student session and its revocable browser
nonce. It lists only that account's current or past membership anchors, with
bounded pagination. Each Class uses the existing Program lock and membership
reader in a separate transaction. Current learning access uses an exact
Program/Class Classroom capability, including required guardian participation,
Class state, entitlement, membership periods and holds. Personal Full or another
Class cannot make an unavailable Class appear authorized. No internal source
evidence, roster or other student's identity is rendered.

Homework reuses saved `WeeklyPlan` and `WeeklyInstrumentPlan` records and
`weekly_tasks.homework_assignment_progress`. The page cannot issue a plan,
generate an assignment, settle a task or grant a reward. Explicit student choices
call the existing `/weekly/plan` and
`/weekly/plan/instruments/current/start` endpoints, with the rendered account
header and canonical start context. Focus, issued Game Mode, saved instrument
recipes, two slots and week boundaries retain their existing meanings. Preference
changes never regenerate saved work. No plan or assignment displays an empty
state, rather than invented participation or completion. Displayed qualification
is the accepted matching-submission approximation, not measured playing or a
director assessment. Notes and reward references are not projected.

Weekly Loop remains an independent account feature. Losing Class participation
or entitlement removes Class learning access without removing separately
authorized Open Weekly Loop work. Account/parent withdrawal or the Weekly Loop
gate denies Homework. The page does not activate Weekly Loop or grant Full.

Membership, comprehensive reporting and individual P-Chart sharing remain
separate. The 41-field reporting contract, 26 exclusions, rating calculations,
historical boundaries, under-13 reporting denial, and school/P-Chart legal
allowlists are unchanged. The school agreement and protected Classroom P-Chart
paths remain disabled pending their existing legal reviews.

Responses and failures are not cached. Leaving or hiding the page clears its
private content; returning reloads current authorization. Mutations never retry
automatically after an ambiguous response. A refresh shows the canonical saved
result before another choice.

Director's Choice is tracked separately in
[classroom-directors-choice-requirements.md](classroom-directors-choice-requirements.md).
