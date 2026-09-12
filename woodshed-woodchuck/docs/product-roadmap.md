# Woodshed Woodchuck — Product Roadmap

_Last updated: September 11, 2026_

## Product vision

Woodshed Woodchuck is a gamified music-practice platform for students, teachers, band directors, studios, and trusted adults.

The product should make practice feel rewarding without turning music into homework. Students get a playful world, visible progress, contests, rewards, and useful practice tools. Adults get enough verification and reporting to encourage good habits without becoming a surveillance system.

The long-term opportunity is broader than a single student practice tracker: Woodshed Woodchuck can become a shared practice environment for individual students, private studios, school bands, and other music programs.

## Primary users

- **Students** — practice, track progress, compete, earn rewards, customize their Woodchuck, and use practice tools.
- **Band directors / teachers** — quickly understand how a group is doing and identify students who may need attention.
- **Trusted verifiers** — parents, guardians, teachers, directors, and other approved adults who can review submitted practice.
- **Private studios / small programs** — potential future customers who need student engagement plus lightweight reporting.

## Product principles

1. **Practice first.** Gamification should support real practice rather than replace it.
2. **Students should not be punished for unavailable adults.** Open participation remains valid; Verified and Pristine activity can add value without excluding students.
3. **Reward consistency without punishing different work styles.** Concentrated practice still counts; frequent practice receives only a modest advantage.
4. **Keep student-facing screens playful. Keep adult-facing screens fast and useful.**
5. **Competition should be motivating, not exclusionary.** Open and Verified divisions should remain available where practical.
6. **Preserve progress and history.** Seasonal competition can reset while earned achievements and Hall of Champions history remain meaningful.
7. **Build the web product well before expanding platform complexity.**

---

## Status vocabulary

- **Live** — part of the established product baseline.
- **Built locally** — implemented and tested in the current development checkout but not yet part of the next public deployment.
- **In progress** — actively being developed or completed.
- **Next** — intended for the next focused development cycle.
- **Later** — valuable, but not required for the next release.
- **Parked** — intentionally deferred.

---

# Current product state

## Student accounts and practice

**Live**

- Woodchuck ID + PIN student accounts.
- Student profiles with instrument and level.
- P-Chart practice logging.
- Multiple trusted verifiers per student.
- Separate verifier identities and PINs.
- Approval / rejection workflows for submitted P-Charts.
- Practice totals and progress tracking.
- Practice timers and core practice utilities.
- Persistent student progress.

## SHED

**Live / Built locally**

The SHED is the main student home environment and should remain the center of the student experience.

Current direction:

- Cabin-based portrait layout.
- Student information and practice controls arranged around the character.
- Metronome and other practice controls accessible from the SHED.
- Placeable / collectible decorative elements.
- Instrument-specific Woodchuck character art.

Latest local work adds:

- Character-free cabin background.
- Separate transparent Woodchuck character layer.
- Production character mapping for:
  - Saxophone
  - Trumpet
  - Percussion
- Idle breathing.
- Practice sway while the metronome is running.
- Success hop on appropriate SHED interactions.
- Tap / wiggle character interaction.
- Maintained responsive 5×2 control layout.
- Removal of temporary runtime staging hacks.

## Artwork coverage

**In progress**

The app supports a broader instrument list than the finished character-art library.

Production-ready transparent character coverage currently includes:

- Saxophone
- Trumpet
- Percussion

Additional instrument characters still need consistent Woodshed Woodchuck artwork. Guitar has been explored but is not yet locked as production art.

The next art pass should prioritize consistent style, transparent character assets, and reusable framing rather than rebuilding whole cabin scenes for every instrument.

## Contests, ratings, and leaderboards

**Live**

The competition system includes:

- Seasons and contest weeks.
- Open and Verified participation.
- Pristine practice as a distinct practice category.
- Student and team standings.
- Weekly leaderboard logic.
- Team membership and emblems.
- Seasonal competition history.
- Dandelion rewards.
- Crown progress and contest achievements.
- Hall of Champions concepts / history.
- Privacy-aware student standings.
- Tie-aware ranking.

The design principle remains: lack of adult or school participation should not prevent a student from competing.

## Student Practice Rating

**Built locally**

A weekly Student Practice Rating gives directors a compact way to understand current student practice health.

Current design:

- Core rating is primarily driven by practice minutes.
- Rating minutes are capped so extremely large totals do not dominate.
- Practice frequency provides only a small consistency benefit.
- Any Verified work during the rating week adds a small bonus.
- Any Pristine work during the rating week adds a small bonus.
- A rating can exceed 100, with a current maximum of 104.
- Missing adult verification never lowers the student's core score.
- Incomplete current weeks are not used for rating/trend calculations.
- Trend arrows compare completed performance with the student's recent completed-week baseline.

The exact formula is intentionally treated as product logic rather than prominently explained in the UI.

## Band Director Dashboard

**Built locally**

The Band Director Dashboard is now designed for fast scanning of a potentially large roster rather than detailed P-Chart-by-P-Chart review.

Top-level experience:

- Band director name.
- Large **Program Rating** with trend arrow.
- Week selector.
- Dense sortable student table.

Current table columns:

1. Name
2. Rating
3. Trend
4. Total Min Wk
5. Verified Min Wk
6. Pristine Min Wk
7. Total Min Life
8. Verified Min Life
9. Pristine Min Life
10. Charts Wk
11. Charts Life
12. Team

Every column is sortable. Team is intentionally placed at the far right and does not group students into large repeated sections.

The Program Rating is based on student ratings and includes zero-practice students so it reflects the health of the whole connected program.

Trusted verifiers who have accepted active band-director relationships receive a Band Director Dashboard link. Detailed P-Chart review remains in the existing Trusted Verifier workflow.

Known limitation:

- Historical director-roster snapshots do not yet exist. Historical program calculations therefore use the currently authorized roster. This is acceptable for the current product stage and does not require immediate schema expansion.

## Rewards, Shop, and customization

**Live / In progress**

Existing systems include:

- Dandelions.
- Crowns / crown progress.
- Stickerbook concepts and collectible decoration.
- SHED decoration / placement.
- Achievement feedback and confetti.
- Login streaks and other engagement mechanics.
- Shop experience / shell.

The Shop should eventually become a meaningful place to spend earned dandelions on cosmetic and customization items rather than merely a placeholder.

## Games and bonus activities

**Live / Evolving**

Existing work includes:

- Plunge / Burrow game.
- Persistent high scores.
- Trivia and challenge concepts.
- Bonus activities.
- Contest-linked activities.

These should remain secondary to actual music practice.

---

# Next release priorities

## 1. Finish the production artwork set needed for release

**Next**

Goal: remove the largest remaining visual inconsistency in the student experience.

Priorities:

- Establish one canonical Woodchuck character style.
- Create transparent character assets for the most important remaining instruments.
- Avoid baking the SHED cabin into individual character art.
- Preserve consistent scale and positioning between instruments.
- Decide which candidate art becomes production and remove ambiguity between prototypes and final assets.

A complete 17-instrument library does not have to block every development task, but the next public SHED push should have enough art coverage to feel intentional rather than unfinished.

## 2. Release-gate QA for SHED + Band Director work

**Next**

Before the next public deployment:

- Test the production SHED on desktop.
- Test the production SHED on an actual Android phone.
- Verify instrument switching.
- Verify motion behavior.
- Verify metronome-triggered practice sway.
- Verify SHED control interactions.
- Verify SHOP dandelion behavior.
- Verify Trusted Verifier → Band Director navigation.
- Verify Program Rating, week navigation, trend arrows, and table sorting.
- Confirm no prototype routes/assets are accidentally shipped as production dependencies.
- Confirm unrelated KHJW work remains isolated.

## 3. Decide the next student-facing feature cycle

**Next**

After the artwork/release gate, choose one focused student-facing development slice rather than opening several large features at once.

Strong candidates:

- Expand rewards/customization.
- Improve contests and seasonal challenges.
- Add more useful practice challenges.
- Strengthen the Shop.
- Improve achievement presentation.
- Continue Band Camp / school-year seasonal content.

---

# Roadmap by product lane

## A. Student practice experience

### Next

- Continue simplifying practice entry and progress feedback.
- Make current-week progress immediately understandable.
- Strengthen useful practice challenges without creating busywork.
- Preserve fast access to metronome, tuner, and practice tools.

### Later

- More advanced exercises and guided challenges.
- Instrument-specific practice suggestions.
- Optional progressive practice paths.
- More meaningful practice-history views for students.

---

## B. SHED, customization, and identity

### In progress

- Complete instrument-specific Woodchuck artwork.
- Keep character motion subtle and charming.
- Maintain clean responsive layout.

### Next

- Expand useful collectible / cosmetic customization.
- Improve the relationship between achievements, Shop purchases, and SHED decoration.
- Make student personalization feel valuable without cluttering the core practice workflow.

### Later

- Additional clothing / hats / accessories.
- More room customization.
- Seasonal SHED themes.
- Additional character reactions and celebrations.

---

## C. Competitions and seasonal play

### Live / Evolving

- Open and Verified divisions.
- Weekly competition.
- Team competition.
- Seasonal structure.
- Dandelions and crowns.
- Hall of Champions history.

### Next

- Refine contest presentation around school-year seasons.
- Continue Band Camp / Back-to-School seasonal programming.
- Add contests only when scoring rules are easy to explain and hard to exploit.
- Keep daily caps / controlled extra-credit concepts where appropriate.

### Later

- More rotating contest categories.
- Expanded trivia / readiness challenges.
- Special school or studio events.
- Additional achievement classes.

---

## D. Band Director and adult tools

### Built locally

- Condensed Band Director Dashboard.
- Program Rating.
- Student ratings and trend arrows.
- Weekly / lifetime practice statistics.
- Verified / Pristine practice metrics.
- Sortable roster.
- Trusted Verifier review workflow.

### Later

Only add complexity after real use shows it is needed.

Possible future additions:

- Student detail page.
- Optional team filter for very large programs.
- Historical roster snapshots for historically exact program ratings.
- Program-level practice trend visualization.
- Export/report tools.
- Additional school/program administration features.

Avoid turning the main dashboard back into a detailed P-Chart log.

---

## E. Rewards, Shop, stickerbook, and achievements

### In progress

- Preserve Dandelion economy.
- Preserve crown progress.
- Continue stickerbook / collectible concepts.
- Maintain celebratory feedback.

### Next

- Give the Shop a useful first catalog.
- Tie purchases to personalization rather than competitive advantage.
- Expand achievement presentation.

### Later

- More cosmetic inventory.
- Limited seasonal items.
- Special event rewards.
- Additional long-term collection goals.

---

## F. Mobile and app experience

### Current direction

The responsive web product remains the primary product.

Home-screen installation / PWA-style access already provides a lightweight app-like path without requiring full native development.

### Later

Consider a dedicated native mobile application only if the web product proves that users need capabilities the browser/PWA experience cannot provide well.

Do not let native-app work delay product validation.

---

## G. Business and monetization

### Exploration

Potential customer / revenue paths include:

- Private music teachers.
- Small teaching studios.
- Band directors / school programs.
- Student/family premium subscriptions.

Potential paid value:

- Larger customization catalog.
- Premium cosmetics / SHED items.
- More advanced exercises and challenges.
- Studio/director reporting and management tools.
- Program features for larger groups.

Core practice participation should remain useful without forcing students into a paywall.

### Next business milestone

Before choosing pricing, get the product into the hands of real students and at least a few real teachers/directors and learn:

- What makes students return?
- Which rewards students actually care about?
- Which director statistics are genuinely useful?
- Whether directors would actively manage students inside WW.
- Which features a teacher/studio would pay to keep.

---

# Release gate for the next public WW push

The next meaningful public push should happen when:

- [ ] Current production SHED behavior is stable.
- [ ] Selected production character artwork is ready.
- [ ] Major supported instruments have acceptable visual coverage.
- [ ] Instrument switching has been verified.
- [ ] SHED motion has been verified on desktop and Android.
- [ ] SHOP dandelion interaction remains correct.
- [ ] Band Director Dashboard is verified in the normal application.
- [ ] Trusted Verifier workflows remain correct.
- [ ] Relevant automated tests pass or known unrelated failures are explicitly documented.
- [ ] Prototype/candidate files are not accidentally promoted.
- [ ] Sibling KHJW changes remain isolated.
- [ ] Production deployment is deliberate rather than an incidental consequence of cleanup work.

---

# Later / optional expansion

These ideas are valuable but should not distract from product validation:

- Native mobile app.
- Larger school/district administration.
- Deep analytics.
- Complex historical reporting.
- Large-scale teacher content-authoring system.
- Extensive social networking.
- Advanced marketplace/e-commerce.
- Additional game rooms that do not directly support practice.
- AI-generated coaching features.

---

# Parked / intentionally deferred

- Rebuilding the SHED architecture again without a demonstrated problem.
- Reintroducing detailed recent-P-Chart logs into the main Band Director Dashboard.
- Making verification mandatory for student participation.
- Reward systems that heavily favor students with more available adults.
- Large backend/schema expansions solely to perfect historical analytics before real users need them.
- Native-app development simply for the sake of having an app.
- Shipping prototype/candidate artwork because it happens to exist in the repository.

---

# Product focus

The near-term goal is not to add the largest possible feature list.

The goal is to make the existing Woodshed Woodchuck loop feel complete:

**Practice → record progress → receive feedback → earn something → customize / compete → return to practice.**

For directors:

**Open dashboard → understand the program immediately → identify who needs attention → review practice when necessary.**

When those two loops work reliably for real students and teachers, the next product and business decisions become much easier.
