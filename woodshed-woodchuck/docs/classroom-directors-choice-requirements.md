# Director’s Choice — approved saved-assignment contract

Leadership approved this contract on November 2, 2026, after the student home
checkpoint `7ef3583`. The earlier saved-assignment questions are resolved by that
approval. This implementation is local only; it does not authorize activation.

- A current, authenticated teacher assigned to an exact active Program/Class may
  create assignments for that Class while its Classroom entitlement is current.
  Program ownership, administrative roles, school email, or another Class do not
  confer teaching authority.
- Content is an existing Homework recipe or short custom music-practice
  instructions. Recipe selection uses the existing instrument, Focus, Game Mode,
  and weekly rotation. It saves the instructions without borrowing a student's
  history, personal target, or suggested block duration. Custom content is plain
  text, with a title up to 120 characters and instructions up to 1,000 characters.
- An assignment belongs to one Monday–Sunday week in America/Chicago. Its week is
  fixed when created. Current and future weeks accept new work; past weeks are
  read-only. Expiration is the following Monday at midnight Central, including
  daylight-saving transitions. Directors select another week to view older work.
- All current eligible members of the selected Class are the audience. Joining
  later in the week makes the Class instructions available; leaving or losing
  current access removes that access. No copied recipient list or student activity
  history is created. These are Class instructions, not student practice records.
- Current teachers in that Class can edit or cancel active, unexpired work. Each
  change appends a version with its author’s exact teaching-assignment reference
  and timestamp. Cancellation is retained and read-only; create new work to replace
  it. A stale edit must reload the current version. Replaying an unchanged creation
  form returns its saved assignment instead of creating a duplicate.
- Work and version provenance survive teacher changes. Former teachers lose access.
  Existing guardian participation rules still require renewed permission when the
  teacher set changes. A replacement teacher never grants new student permission.
- Students see the latest saved version, its week, and its scheduled/current/
  cancelled/expired status. Director’s Choice has its own section, separate from
  personalized Weekly Loop Homework. Assignments from multiple Classes retain
  their Program and Class labels. Directors can inspect earlier versions.
- No completion evidence, grades, attendance, practice credit, assessment, rewards,
  new reporting fields, or additional permissions are authorized. Existing practice
  submissions do not prove completion of a director assignment.

Membership, comprehensive reporting, and individual P-Chart sharing remain
independent. A P-Chart invitation does not grant assignment access. Account age,
qualified guardian participation, current membership, and exact Class entitlement
must all remain valid. Under-13 comprehensive reporting and legally unapproved
school/P-Chart authorization paths remain denied. The 41 approved reporting fields
and 26 exclusions are unchanged.

The implementation uses an additive migration, `c36choice001`, with two tables:
Class assignments and immutable saved versions. It performs no historical backfill.
A populated downgrade is refused. The feature gate
`CLASSROOM_DIRECTORS_CHOICE_ENABLED` defaults off and also requires the existing
Classroom/enrollment gates. No environment or activation setting is changed.

Screens: `/classroom/directors-choice` for PIN-confirmed director management;
`/classroom` and exact-Class student assignment pages for owned student access.
Reads do not issue personal Homework, write activity, or change permissions.

Next UI task: integrate the saved Class assignments into the student's Weekly
Newsletter Director’s Choice lane, retaining the same current authorization and
Class labels. That Newsletter lane remains unavailable in this change. Completion
tracking remains outside approval; any future request needs its own evidence and
disclosure contract.

The existing school agreement and protected P-Chart legal-review requirements are
recorded in [classroom-school-authorization.txt](classroom-school-authorization.txt).
They are not bypassed or resolved by this assignment feature.
