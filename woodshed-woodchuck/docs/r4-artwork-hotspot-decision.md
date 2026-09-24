# R4A artwork hotspot decision

Implemented 2026-09-24 after the approved artwork was staged by the user.
This supersedes the earlier deferred-artwork note.

SHED and SHOP each have exactly ten full-cell targets in a 2-column × 5-row
CSS grid. Cells are 50% wide and 20% high. Art and grid share one contained,
uncropped rectangle at the image's natural aspect ratio. Fixed navigation and
safe-area chrome are outside that rectangle. No polygons or object hitboxes.

| Row | SHED left | SHED right | SHOP left | SHOP right |
| --- | --- | --- | --- | --- |
| 1 | Name/setup | Team chooser | Dandelions | Gear Shelf |
| 2 | Your Woodchuck | Student level | Crown Progress | Little Buddy Shelf |
| 3 | XP details | Metronome | GOAT Tracker | Share Woodshed |
| 4 | Stickerbook | Tuner | Artist | Premium |
| 5 | Mum | Audio settings | Practice Rooms | Practice Definition |

Tab order follows rows: L1, R1, L2, R2, through L5, R5. Transparent semantic
buttons and the Premium link use visible keyboard focus. The character layers
cannot receive pointer events or become additional room controls. The hidden
Secret dot remains separate, immediately below the SHED scene, so it cannot
cover L5 on landscape phones.

Your Woodchuck editing belongs only to SHED L2. SHOP renders the same saved
instrument, hoodie and hat with the shared colorizer, without another editor.
The user's staged images and intentional legacy deletions remain untouched.
