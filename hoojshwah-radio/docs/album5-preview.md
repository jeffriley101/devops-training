# KHJW Album 5 Preview audio staging

The preview is intentionally separate from `data/tracks.json` and every file under
`data/playlists/`. Production audio belongs in the existing
`hoojshwah-radio-audio` R2 bucket. Track 9 reuses the existing
`audio/chasin-falls.mp3` object and must not be uploaded again.

The source recordings were not present in `/home/geph/Downloads` on 2026-08-27.
Run the preparation commands only after the approved files are restored there.
This laptop also needs `ffmpeg`/`ffprobe` installed before preparation. The MP3
sources are copied without re-encoding; WAV sources are converted to MP3 for the
existing KHJW delivery format.

| # | Source filename | R2 object |
|---:|---|---|
| 1 | `HJW Like Boom 2022_08_04_22_07_03.mp3` | `audio/album5-preview/01-like-boom.mp3` |
| 2 | `HW_Runnin_Groves_2102022.wav` | `audio/album5-preview/02-runnin-groves.mp3` |
| 3 | `HW Track is Burnin 2025_12_18_18_39_26.mp3` | `audio/album5-preview/03-track-is-burnin.mp3` |
| 4 | `HW_Clean_Colorful_3172022.wav` | `audio/album5-preview/04-clean-colorful.mp3` |
| 5 | `HW Brush it Off 2024_08_29_19_56_34.mp3` | `audio/album5-preview/05-brush-it-off.mp3` |
| 6 | `HW Things to Say 2025_08_07_18_41_57.mp3` | `audio/album5-preview/06-things-to-say.mp3` |
| 7 | `HW Low Lyer 2025_11_20_19_56_16.mp3` | `audio/album5-preview/07-low-lyer.mp3` |
| 8 | `HW_Take_Me_Out_41521.wav` | `audio/album5-preview/08-take-me-out.mp3` |
| 9 | `HW Chasin Falls 2024_07_18_19_40_09 (1).mp3` | `audio/chasin-falls.mp3` (reuse) |
| 10 | `HW Stand It 2023_08_17_18_43_43.mp3` | `audio/album5-preview/10-stand-it.mp3` |
| 11 | `HW_Sleep_Often_Dream_Walker_3312022.wav` | `audio/album5-preview/11-sleep-often-dream-walker.mp3` |

Create a staging directory and prepare the ten new objects:

```bash
mkdir -p /home/geph/Downloads/khjw-album5-preview-staging
cp "/home/geph/Downloads/HJW Like Boom 2022_08_04_22_07_03.mp3" "/home/geph/Downloads/khjw-album5-preview-staging/01-like-boom.mp3"
ffmpeg -i "/home/geph/Downloads/HW_Runnin_Groves_2102022.wav" -codec:a libmp3lame -q:a 2 "/home/geph/Downloads/khjw-album5-preview-staging/02-runnin-groves.mp3"
cp "/home/geph/Downloads/HW Track is Burnin 2025_12_18_18_39_26.mp3" "/home/geph/Downloads/khjw-album5-preview-staging/03-track-is-burnin.mp3"
ffmpeg -i "/home/geph/Downloads/HW_Clean_Colorful_3172022.wav" -codec:a libmp3lame -q:a 2 "/home/geph/Downloads/khjw-album5-preview-staging/04-clean-colorful.mp3"
cp "/home/geph/Downloads/HW Brush it Off 2024_08_29_19_56_34.mp3" "/home/geph/Downloads/khjw-album5-preview-staging/05-brush-it-off.mp3"
cp "/home/geph/Downloads/HW Things to Say 2025_08_07_18_41_57.mp3" "/home/geph/Downloads/khjw-album5-preview-staging/06-things-to-say.mp3"
cp "/home/geph/Downloads/HW Low Lyer 2025_11_20_19_56_16.mp3" "/home/geph/Downloads/khjw-album5-preview-staging/07-low-lyer.mp3"
ffmpeg -i "/home/geph/Downloads/HW_Take_Me_Out_41521.wav" -codec:a libmp3lame -q:a 2 "/home/geph/Downloads/khjw-album5-preview-staging/08-take-me-out.mp3"
cp "/home/geph/Downloads/HW Stand It 2023_08_17_18_43_43.mp3" "/home/geph/Downloads/khjw-album5-preview-staging/10-stand-it.mp3"
ffmpeg -i "/home/geph/Downloads/HW_Sleep_Often_Dream_Walker_3312022.wav" -codec:a libmp3lame -q:a 2 "/home/geph/Downloads/khjw-album5-preview-staging/11-sleep-often-dream-walker.mp3"
```

After listening approval, upload with Wrangler 4 (these commands intentionally
retain `--remote` and set the content type explicitly):

```bash
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/01-like-boom.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/01-like-boom.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/02-runnin-groves.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/02-runnin-groves.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/03-track-is-burnin.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/03-track-is-burnin.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/04-clean-colorful.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/04-clean-colorful.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/05-brush-it-off.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/05-brush-it-off.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/06-things-to-say.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/06-things-to-say.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/07-low-lyer.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/07-low-lyer.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/08-take-me-out.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/08-take-me-out.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/10-stand-it.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/10-stand-it.mp3" --content-type audio/mpeg --remote
npx wrangler@4 r2 object put hoojshwah-radio-audio/audio/album5-preview/11-sleep-often-dream-walker.mp3 --file "/home/geph/Downloads/khjw-album5-preview-staging/11-sleep-often-dream-walker.mp3" --content-type audio/mpeg --remote
```

Before deployment, use `ffprobe` to record exact durations in
`data/album5-preview.json`, then verify all eleven public URLs return HTTP 200 and
`Content-Type: audio/mpeg`.
