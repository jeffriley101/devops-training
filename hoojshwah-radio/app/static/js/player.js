const audio = document.querySelector("#radio-audio");
const playButton = document.querySelector("#play-button");
const resyncButton = document.querySelector("#resync-button");
const nowTitle = document.querySelector("#now-title");
const nowArtist = document.querySelector("#now-artist");
const upNext = document.querySelector("#up-next");

let station = null;
let currentTrack = null;

function getLoopPosition(totalDurationSeconds) {
  const nowSeconds = Math.floor(Date.now() / 1000);
  return nowSeconds % totalDurationSeconds;
}

function findCurrentTrack(tracks, loopPositionSeconds) {
  let elapsed = 0;

  for (let index = 0; index < tracks.length; index += 1) {
    const track = tracks[index];
    const nextElapsed = elapsed + track.duration_seconds;

    if (loopPositionSeconds < nextElapsed) {
      const offsetSeconds = loopPositionSeconds - elapsed;
      const nextTrack = tracks[(index + 1) % tracks.length];

      return {
        track,
        nextTrack,
        offsetSeconds
      };
    }

    elapsed = nextElapsed;
  }

  return {
    track: tracks[0],
    nextTrack: tracks[1] || tracks[0],
    offsetSeconds: 0
  };
}

function renderTrackInfo(result) {
  currentTrack = result.track;
  nowTitle.textContent = result.track.title;
  nowArtist.textContent = result.track.artist;
  upNext.textContent = result.nextTrack.title;
}

function tuneStation() {
  const loopPosition = getLoopPosition(station.total_duration_seconds);
  const result = findCurrentTrack(station.tracks, loopPosition);

  renderTrackInfo(result);

  audio.src = result.track.audio_url;

  audio.addEventListener(
    "loadedmetadata",
    () => {
      if (Number.isFinite(audio.duration) && result.offsetSeconds < audio.duration) {
        audio.currentTime = result.offsetSeconds;
      }
    },
    { once: true }
  );
}

async function loadStation() {
  const response = await fetch("/api/station");
  station = await response.json();

  tuneStation();
}

resyncButton.addEventListener("click", () => {
  if (!station) {
    return;
  }

  tuneStation();
  resyncButton.textContent = "Signal Resynced";

  window.setTimeout(() => {
    resyncButton.textContent = "Resync Signal";
  }, 1400);
});

playButton.addEventListener("click", async () => {
  if (!station) {
    return;
  }

  tuneStation();

  try {
    await audio.play();
    playButton.textContent = "Signal Playing";
  } catch (error) {
    console.error("Could not play audio:", error);
    playButton.textContent = "Signal Blocked";
  }
});

audio.addEventListener("ended", () => {
  tuneStation();
  audio.play();
});

loadStation();
