# Woodshed Woodchuck Phase 4 Current State

Branch:

`ww-phase-4-woodshed-core`

## Completed Phase 4 work

Recent commits:

- `212776c` - Add Phase 4 Woodshed architecture plan
- `f39ae19` - Add P-Book summary cards
- `4d699d5` - Remember recent P-Chart email contacts
- `6ebf137` - Upgrade P-Book practice chart flow
- `e263ce9` - Update Woodshed navigation labels
- `0260bd9` - Use dandelions and camp sign nav labels
- `d278411` - Add P-Chart dandelion reward formula

## Current navigation

Bottom nav labels now use camp-sign style:

`SHED | BOOK | BOARD | SHOP`

Routes are still:

- `/home`
- `/p-book`
- `/quest`
- `/store`

Do not rename routes yet unless doing a deliberate routing pass.

## Book / P-Chart status

The Book tab is the most current Phase 4 page.

Implemented:

- Whistler guidance card
- practice timer
- timer stop asks whether to enter elapsed minutes
- timer auto-fills Minutes Practiced
- manual minutes override remains possible
- practice date
- optional short note
- expandable practice detail checkboxes
- Submit to P-Book
- Email P-Chart
- Copy P-Chart
- recent teacher/parent email suggestions saved locally
- Lifetime Minutes summary
- Practice Days summary
- Charts in Book summary
- P-Book history
- checked practice details are saved to `practiceLog`
- checked practice details appear in history/export text
- success message has a green callout

## Dandelion economy status

User-facing currency is now `dandelions`.

P-Chart reward formula:

- 5 practice minutes = 1 dandelion
- each checked practice detail = +1 dandelion
- max 75 dandelions per day from saved entries

Current implementation still stores dandelions internally in legacy fields:

- `progress.credits`
- `rewardCredits`
- `creditsAwarded`
- `reward_credits`

This is intentional for localStorage compatibility. Do not rename internal state casually.

## Important product language

Use:

- dandelions, not credits
- Book / P-Book / P-Chart
- SHED / BOOK / BOARD / SHOP
- Whistler as the Book practice coach
- Yek-Yek for the Board later
- The Viking Sax for the Shop
- John as chipmunk Section Leader pop-in

## Not done yet

Still needs future work:

- Board page redesign into Camp Board
- Yek-Yek Board announcer
- seasonal event/crown foundation
- Shop page rename/polish into The Viking Sax's Dandelion Shop
- visible remaining page headers on Shed, Board, Shop
- internal credits-to-dandelions refactor, if desired
- dandelion economy harmonization between Quest/Board and P-Charts
- growth stage/crown stubs
- artist handoff packet
- README update
- merge to main / push / deploy after smoke test
