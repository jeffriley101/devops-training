# GOAT Tracker clip pool

No audio recordings are included yet. Add only user-supplied, original,
public-domain, or clearly licensed short baby-goat recordings.

Expected local filenames:

- `baby-goat-1.mp3`
- `baby-goat-2.mp3`
- `baby-goat-3.mp3`
- `baby-goat-4.mp3`

After approved files are added here, list their local URLs in
`GOAT_CLIP_URLS` in `static/js/audio.js`. The pool accepts three to five clips,
uses `Tone.Player`, routes through the shared effects master, stops overlap, and
avoids immediately repeating the same loaded clip.
