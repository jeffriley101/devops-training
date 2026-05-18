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
const trackRecordingInfo = document.querySelector("#track-recording-info");
const loopLength = document.querySelector("#loop-length");
const trackListToggle = document.querySelector("#track-list-toggle");

let station = null;

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
  }

  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

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
      return {
        track,
        nextTrack: tracks[(index + 1) % tracks.length],
        offsetSeconds: loopPositionSeconds - elapsed
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

function renderLoopLength(totalSeconds) {
  if (!loopLength) {
    return;
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.round((totalSeconds % 3600) / 60);

  if (hours > 0 && minutes > 0) {
    loopLength.textContent = `Current broadcast loop: about ${hours} hr ${minutes} min`;
  } else if (hours > 0) {
    loopLength.textContent = `Current broadcast loop: about ${hours} hr`;
  } else {
    loopLength.textContent = `Current broadcast loop: about ${minutes} min`;
  }
}

function getRecordingInfo(track) {
  if (track.recorded_date && track.recorded_location) {
    return `Recorded: ${track.recorded_date} · ${track.recorded_location}`;
  }

  if (track.recording_context) {
    return track.recording_context;
  }

  return "";
}

function renderTrackList(tracks) {
  if (!trackList) {
    return;
  }

  trackList.innerHTML = "";

  tracks.forEach((track) => {
    const item = document.createElement("li");
    const typeLabel = track.type === "bumper" ? "Station ID" : "Track";
    const recordingInfo = getRecordingInfo(track);

    item.innerHTML = `
      <span class="playlist-title">${track.title} — ${track.artist} (${typeLabel})</span>
      ${recordingInfo ? `<span class="playlist-meta">${recordingInfo}</span>` : ""}
    `;

    trackList.appendChild(item);
  });
}

function renderTrackInfo(result) {
  nowTitle.textContent = result.track.title;
  nowArtist.textContent = result.track.artist;
  upNext.textContent = result.nextTrack.title;

  if (trackRecordingInfo) {
    trackRecordingInfo.textContent = getRecordingInfo(result.track);
  }

  if (trackProgress) {
    trackProgress.textContent = `Tuned in at ${formatDuration(result.offsetSeconds)} of ${formatDuration(result.track.duration_seconds)}`;
  }
}

function tuneStation() {
  if (!station || !station.tracks || station.tracks.length === 0) {
    return;
  }

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
    const response = await fetch("/api/station", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Station API returned ${response.status}`);
    }

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

if (trackListToggle && trackList) {
  trackListToggle.addEventListener("click", () => {
    const isCollapsed = trackList.classList.toggle("collapsed");
    trackListToggle.textContent = isCollapsed ? "Show Playlist" : "Hide Playlist";
  });
}

if (shareButton) {
  shareButton.addEventListener("click", async () => {
    const stationUrl = "https://hoojshwah-radio-live.onrender.com/";

    try {
      await navigator.clipboard.writeText(stationUrl);
      if (shareStatus) {
        shareStatus.textContent = "Station link copied.";
      }
    } catch (error) {
      console.error("Could not copy station link:", error);
      if (shareStatus) {
        shareStatus.textContent = stationUrl;
      }
    }
  });
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
  if (!station) {
    return;
  }

  tuneStation();
  audio.play();
});

loadStation();
