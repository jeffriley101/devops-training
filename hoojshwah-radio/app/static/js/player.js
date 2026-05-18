const audio = document.querySelector("#radio-audio");
const playButton = document.querySelector("#play-button");
const resyncButton = document.querySelector("#resync-button");
const nowTitle = document.querySelector("#now-title");
const nowArtist = document.querySelector("#now-artist");
const upNext = document.querySelector("#up-next");
const trackList = document.querySelector("#track-list");
const shareButton = document.querySelector("#share-button");
const shareStatus = document.querySelector("#share-status");
const trackProgress = document.querySelector("#track-progress");
const loopLength = document.querySelector("#loop-length");
const trackListToggle = document.querySelector("#track-list-toggle");

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

function renderTrackList(tracks) {
  trackList.innerHTML = "";

  tracks.forEach((track) => {
    const item = document.createElement("li");
    item.textContent = `${track.title} — ${track.artist}`;
    trackList.appendChild(item);
  });
}

function renderTrackInfo(result) {
  currentTrack = result.track;
  nowTitle.textContent = result.track.title;
  nowArtist.textContent = result.track.artist;
  upNext.textContent = result.nextTrack.title;

  if (trackProgress) {
    trackProgress.textContent = `Tuned in at ${formatDuration(result.offsetSeconds)} of ${formatDuration(result.track.duration_seconds)}`;
  }
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
  try {
    const response = await fetch("/api/station");
    station = await response.json();

    renderTrackList(station.tracks);
    renderLoopLength(station.total_duration_seconds);
    tuneStation();
  } catch (error) {
    console.error("Could not load station:", error);
    nowTitle.textContent = "Station temporarily unavailable";
    upNext.textContent = "try refreshing";
  }
}

resyncButton.addEventListener("click", () => {
  if (!station) {
    return;
  }

  renderTrackList(station.tracks);
  tuneStation();
  resyncButton.textContent = "Signal Resynced";

  window.setTimeout(() => {
    resyncButton.textContent = "Resync Signal";
  }, 1400);
});

shareButton.addEventListener("click", async () => {
  const stationUrl = "https://hoojshwah-radio-live.onrender.com/";

  try {
    await navigator.clipboard.writeText(stationUrl);
    shareStatus.textContent = "Station link copied.";
  } catch (error) {
    console.error("Could not copy station link:", error);
    shareStatus.textContent = stationUrl;
  }
});

playButton.addEventListener("click", async () => {
  if (!station) {
    return;
  }

  renderTrackList(station.tracks);
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
  renderTrackList(station.tracks);
  tuneStation();
  audio.play();
});

loadStation();
