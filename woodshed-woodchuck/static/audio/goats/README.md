# GOAT Tracker clip pool

Five approved, locally hosted baby-goat recordings are included. Their source
and processing documentation is retained alongside them.

Expected local filenames:

- `goat-01.mp3`
- `goat-02.mp3`
- `goat-03.mp3`
- `goat-04.mp3`
- `goat-05.mp3`

The pool uses `Tone.Player`, loads only after audio unlock, routes through the
shared effects master, stops overlap, and avoids immediately repeating the same
loaded clip.
