#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="app/static/audio/bumpers"
mkdir -p "$OUT_DIR"

# Voice files
VOICE_DIR="$HOME/Downloads/sb"

# Music bed search roots
BED_A="$HOME/Downloads/drive-download-20260516T235139Z-3-001"
BED_B="$HOME/Downloads/drive-download-20260516T235653Z-3-001"

make_bumper() {
  local name="$1"
  local voice="$2"
  local bed="$3"
  local output="$4"
  local max_seconds="${5:-}"

  echo "Building $output"

  if [[ -n "$max_seconds" ]]; then
    ffmpeg -y \
      -stream_loop -1 -i "$bed" \
      -i "$voice" \
      -filter_complex "[0:a]atrim=0:${max_seconds},asetpts=PTS-STARTPTS,volume=0.16,afade=t=out:st=$((${max_seconds}-3)):d=3[bed];[1:a]volume=1.25[voice];[bed][voice]amix=inputs=2:duration=longest:dropout_transition=0[mix]" \
      -map "[mix]" \
      -codec:a libmp3lame -qscale:a 2 \
      "$OUT_DIR/$output"
  else
    ffmpeg -y \
      -i "$bed" \
      -i "$voice" \
      -filter_complex "[0:a]volume=0.16[bed];[1:a]volume=1.25[voice];[bed][voice]amix=inputs=2:duration=longest:dropout_transition=0[mix]" \
      -map "[mix]" \
      -codec:a libmp3lame -qscale:a 2 \
      "$OUT_DIR/$output"
  fi
}

make_bumper \
  "sb-sm" \
  "$VOICE_DIR/sb-sm.mp3" \
  "$BED_A/firest_hotcut.mp3" \
  "bumper-sm.mp3"

make_bumper \
  "sb-spacement" \
  "$VOICE_DIR/sb-spacement.mp3" \
  "$BED_A/sax_solo_on_the_shelf.mp3" \
  "bumper-spacement.mp3"

make_bumper \
  "sb-spaceradio" \
  "$VOICE_DIR/sb-spaceradio.mp3" \
  "$BED_A/like_boom_hotcut.mp3" \
  "bumper-spaceradio.mp3"

make_bumper \
  "sb-ty" \
  "$VOICE_DIR/sb-ty.mp3" \
  "$BED_A/falling_island_hotcut.mp3" \
  "bumper-ty.mp3"

make_bumper \
  "sb-use" \
  "$VOICE_DIR/sb-use.mp3" \
  "$BED_A/who_i_am_ringtone.mp3" \
  "bumper-use.mp3"

make_bumper \
  "sb-vs" \
  "$VOICE_DIR/sb-vs.wav" \
  "$BED_A/wyley_wyrk_beefcut.mp3" \
  "bumper-vs.mp3" \
  "45"

make_bumper \
  "sb-yac" \
  "$VOICE_DIR/sb-yac.m4a" \
  "$BED_A/on_the_shelf_heaven.mp3" \
  "bumper-yac.mp3"

echo "Done. Created bumpers:"
ls -lh "$OUT_DIR"
