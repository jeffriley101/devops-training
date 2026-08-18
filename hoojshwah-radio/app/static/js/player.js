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
const signalTimer = document.querySelector("#signal-timer");
const signalEvent = document.querySelector("#signal-event");
const trackRecordingInfo = document.querySelector("#track-recording-info");
const reactionPanel = document.querySelector("#reaction-panel");
const reactionButtons = document.querySelectorAll(".reaction-button");
const loopLength = document.querySelector("#loop-length");
const trackListToggle = document.querySelector("#track-list-toggle");
const backstageTrigger = document.querySelector("#backstage-trigger");
const backstageDialog = document.querySelector("#backstage-dialog");
const backstageForm = document.querySelector("#backstage-form");
const backstagePass = document.querySelector("#backstage-pass");

let station = null;
let currentTrackIndex = 0;
let backstageUnlocked = false;
let backstagePickActive = false;
let userWantsPlayback = false;
let playbackRecoveryTimeout = null;

function setMediaSessionPlaybackState(state) {
  if (!("mediaSession" in navigator)) {
    return;
  }

  try {
    navigator.mediaSession.playbackState = state;
  } catch (error) {
    console.warn("Could not update Media Session playback state:", error);
  }
}

function updateMediaSession(track) {
  if (!("mediaSession" in navigator) || typeof MediaMetadata === "undefined" || !track) {
    return;
  }

  navigator.mediaSession.metadata = new MediaMetadata({
    title: track.title,
    artist: track.artist,
    album: "Hoojshwah Radio"
  });
  setMediaSessionPlaybackState(userWantsPlayback ? "playing" : "paused");

  navigator.mediaSession.setActionHandler("play", async () => {
    try {
      userWantsPlayback = true;
      await audio.play();
      setMediaSessionPlaybackState("playing");
      startActivePlaybackTimer();
      playButton.textContent = "Signal Playing";
    } catch (error) {
      setMediaSessionPlaybackState("paused");
      console.error("Could not play from media session:", error);
    }
  });

  navigator.mediaSession.setActionHandler("pause", () => {
    userWantsPlayback = false;
    audio.pause();
    stopActivePlaybackTimer();
    setMediaSessionPlaybackState("paused");
    playButton.textContent = "Play Signal";
  });
}

let activePlaybackSeconds = 0;
let activePlaybackInterval = null;

function renderSignalTimer() {
  if (!signalTimer) {
    return;
  }

  signalTimer.textContent = `Signal Time: ${formatDuration(activePlaybackSeconds)}`;
}

function logSignalEvent(eventName, detail = "") {
  const timestamp = new Date().toLocaleTimeString();
  const trackTitle = nowTitle ? nowTitle.textContent : "unknown track";
  const message = `Signal Event: ${eventName} at ${timestamp}${detail ? ` · ${detail}` : ""}`;

  console.warn(message, {
    eventName,
    detail,
    trackTitle,
    paused: audio.paused,
    currentTime: audio.currentTime,
    readyState: audio.readyState,
    networkState: audio.networkState,
    error: audio.error
  });

  if (signalEvent) {
    signalEvent.textContent = message;
  }
}


function stopActivePlaybackTimer() {
  if (activePlaybackInterval) {
    window.clearInterval(activePlaybackInterval);
    activePlaybackInterval = null;
  }
}

function startActivePlaybackTimer() {
  stopActivePlaybackTimer();

  activePlaybackInterval = window.setInterval(() => {
    activePlaybackSeconds += 1;
    renderSignalTimer();
  }, 1000);
}

function resetActivePlaybackTimer() {
  activePlaybackSeconds = 0;
  renderSignalTimer();
}

function schedulePlaybackRecovery(reason) {
  if (!userWantsPlayback || !station) {
    return;
  }

  if (playbackRecoveryTimeout) {
    window.clearTimeout(playbackRecoveryTimeout);
  }

  console.warn(`Scheduling playback recovery after: ${reason}`);

  playbackRecoveryTimeout = window.setTimeout(async () => {
    playbackRecoveryTimeout = null;

    if (!userWantsPlayback || !audio.paused) {
      return;
    }

    try {
      await audio.play();
      setMediaSessionPlaybackState("playing");
      startActivePlaybackTimer();
      playButton.textContent = backstagePickActive ? "Backstage Signal" : "Signal Playing";
      console.warn("Playback recovery succeeded.");
    } catch (error) {
      console.error("Playback recovery failed:", error);
      setMediaSessionPlaybackState("paused");
      playButton.textContent = "Tap Play to Resume";
    }
  }, 1200);
}


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

function renderTrackProgress(track = null, currentSecondsOverride = null) {
  if (!trackProgress) {
    return;
  }

  const currentTrack = track || station?.tracks?.[currentTrackIndex];
  const fullSeconds = Number.isFinite(audio.duration) && audio.duration > 0
    ? audio.duration
    : currentTrack?.duration_seconds || 0;

  const currentSeconds = Number.isFinite(currentSecondsOverride)
    ? currentSecondsOverride
    : Number.isFinite(audio.currentTime)
      ? audio.currentTime
      : 0;

  trackProgress.textContent = `Track Time: ${formatDuration(currentSeconds)} / ${formatDuration(fullSeconds)}`;
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
	trackIndex: index,
        nextTrack: tracks[(index + 1) % tracks.length],
        offsetSeconds: loopPositionSeconds - elapsed
      };
    }

    elapsed = nextElapsed;
  }

  return {
    track: tracks[0],
    trackIndex: 0,
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

function renderTrackList(tracks, currentTrackId = null) {
  if (!trackList) {
    return;
  }

  trackList.innerHTML = "";

  tracks.forEach((track, index) => {
    const item = document.createElement("li");
    const typeLabel = track.type === "bumper" ? "Station ID" : "Track";
    const recordingInfo = getRecordingInfo(track);

    if (currentTrackId && track.id === currentTrackId) {
      item.classList.add("current-track");
    }

    if (backstageUnlocked) {
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.title = "Play this Backstage Pick";

      item.addEventListener("click", () => {
        playBackstageTrack(index);
      });

      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          playBackstageTrack(index);
        }
      });
    }

    item.innerHTML = `
      <span class="playlist-title">${track.title} — ${track.artist} (${typeLabel})</span>
      ${recordingInfo ? `<span class="playlist-meta">${recordingInfo}</span>` : ""}
    `;

    trackList.appendChild(item);
  });
}

async function loadReactions() {
  try {
    const response = await fetch("/api/reactions", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Reaction API returned ${response.status}`);
    }

    const data = await response.json();
    return data.reactions || {};
  } catch (error) {
    console.error("Could not load reactions:", error);
    return {};
  }
}

async function saveReaction(trackId, emoji) {
  const response = await fetch("/api/reactions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      track_id: trackId,
      emoji
    })
  });

  if (!response.ok) {
    throw new Error(`Reaction API returned ${response.status}`);
  }

  return response.json();
}

async function updateReactionCounts(trackId) {
  if (!reactionPanel || !trackId) {
    return;
  }

  const reactions = await loadReactions();
  const trackReactions = reactions[trackId] || {};

  reactionButtons.forEach((button) => {
    const emoji = button.dataset.emoji;
    const count = trackReactions[emoji] || 0;
    const countLabel = button.querySelector("span");

    if (countLabel) {
      countLabel.textContent = count;
    }
  });
}

function renderTrackInfo(result) {
  nowTitle.textContent = result.track.title;
  nowArtist.textContent = result.track.artist;
  upNext.textContent = result.nextTrack.title;

  updateReactionCounts(result.track.id);
  updateMediaSession(result.track);

  if (trackRecordingInfo) {
    trackRecordingInfo.textContent = getRecordingInfo(result.track);
  }

  if (trackProgress) {
    trackProgress.classList.remove("backstage-pick");
    renderTrackProgress(result.track, result.offsetSeconds);
  }
}

function tuneStation() {
  if (!station || !station.tracks || station.tracks.length === 0) {
    return;
  }

  const loopPosition = getLoopPosition(station.total_duration_seconds);
  const result = findCurrentTrack(station.tracks, loopPosition);

  currentTrackIndex = result.trackIndex;

  renderTrackInfo(result);
  renderTrackList(station.tracks, result.track.id);

  audio.src = result.track.audio_url;

  audio.addEventListener(
    "loadedmetadata",
    () => {
      if (Number.isFinite(audio.duration) && result.offsetSeconds < audio.duration) {
        audio.currentTime = result.offsetSeconds;
      }

      renderTrackProgress(result.track);
    },
    { once: true }
  );
}

function playTrackByIndex(index) {
  if (!station || !station.tracks || station.tracks.length === 0) {
    return;
  }

  currentTrackIndex = index % station.tracks.length;

  const track = station.tracks[currentTrackIndex];
  const nextTrack = station.tracks[(currentTrackIndex + 1) % station.tracks.length];

  const result = {
    track,
    trackIndex: currentTrackIndex,
    nextTrack,
    offsetSeconds: 0
  };

  renderTrackInfo(result);
  renderTrackList(station.tracks, track.id);

  audio.src = track.audio_url;
  audio.currentTime = 0;
  renderTrackProgress(track, 0);
}

async function playBackstageTrack(index) {
  if (!backstageUnlocked || !station || !station.tracks || station.tracks.length === 0) {
    return;
  }

  backstagePickActive = true;
  playTrackByIndex(index);

  if (trackProgress) {
    trackProgress.textContent = "Backstage Pick";
    trackProgress.classList.add("backstage-pick");
  }

  try {
    userWantsPlayback = true;
    resetActivePlaybackTimer();
    await audio.play();
    setMediaSessionPlaybackState("playing");
    startActivePlaybackTimer();
    playButton.textContent = "Backstage Signal";
  } catch (error) {
    setMediaSessionPlaybackState("paused");
    console.error("Could not play Backstage Pick:", error);
    playButton.textContent = "Signal Blocked";
  }
}

function unlockBackstage() {
  backstageUnlocked = true;
  document.body.classList.add("backstage-active");

  if (backstageDialog) {
    backstageDialog.hidden = true;
  }

  if (backstagePass) {
    backstagePass.value = "";
  }

  renderTrackList(station?.tracks || [], station?.tracks?.[currentTrackIndex]?.id);
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
    await renderReactionStandings();
  } catch (error) {
    console.error("Could not load station:", error);
    nowTitle.textContent = "Station temporarily unavailable";
    upNext.textContent = "try refreshing";
  }
}

if (reactionButtons.length > 0) {
  reactionButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const track = station?.tracks?.[currentTrackIndex];
      const emoji = button.dataset.emoji;

      if (!track || !emoji) {
        return;
      }

      try {
        const result = await saveReaction(track.id, emoji);
        const countLabel = button.querySelector("span");

        if (countLabel) {
          countLabel.textContent = result.count;
        }
      } catch (error) {
        console.error("Could not save reaction:", error);
      }
    });
  });
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
    userWantsPlayback = true;
    resetActivePlaybackTimer();
    await audio.play();
    setMediaSessionPlaybackState("playing");
    startActivePlaybackTimer();
    playButton.textContent = "Signal Playing";
  } catch (error) {
    userWantsPlayback = false;
    setMediaSessionPlaybackState("paused");
    console.error("Could not play audio:", error);
    playButton.textContent = "Signal Blocked";
  }
});

audio.addEventListener("play", () => {
  setMediaSessionPlaybackState("playing");
});

audio.addEventListener("playing", () => {
  setMediaSessionPlaybackState("playing");
});

audio.addEventListener("pause", () => {
  stopActivePlaybackTimer();
  setMediaSessionPlaybackState("paused");

  if (userWantsPlayback) {
    schedulePlaybackRecovery("pause");
  }
});

audio.addEventListener("ended", async () => {
  if (!station || !station.tracks || station.tracks.length === 0) {
    return;
  }

  if (backstagePickActive) {
    backstagePickActive = false;

    if (trackProgress) {
      trackProgress.classList.remove("backstage-pick");
    }

    tuneStation();
  } else {
    playTrackByIndex(currentTrackIndex + 1);
  }

  try {
    userWantsPlayback = true;
    await audio.play();
    setMediaSessionPlaybackState("playing");
    startActivePlaybackTimer();
  } catch (error) {
    console.error("Could not continue audio:", error);
    setMediaSessionPlaybackState("paused");
    playButton.textContent = "Tap Play to Resume";
  }
});

["timeupdate", "loadedmetadata", "durationchange", "seeking", "seeked"].forEach((eventName) => {
  audio.addEventListener(eventName, () => {
    renderTrackProgress();
  });
});

["play", "playing", "pause", "waiting", "stalled", "suspend", "error", "abort", "emptied", "ended"].forEach((eventName) => {
  audio.addEventListener(eventName, () => {
    logSignalEvent(eventName, audio.error ? audio.error.message : "");
  });
});

["waiting", "stalled", "error", "abort"].forEach((eventName) => {
  audio.addEventListener(eventName, () => {
    schedulePlaybackRecovery(eventName);
  });
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") {
    return;
  }

  logSignalEvent("visibilitychange:visible");

  if (userWantsPlayback && audio.paused) {
    schedulePlaybackRecovery("visibilitychange");
  }
});

window.addEventListener("online", () => {
  logSignalEvent("online");
  schedulePlaybackRecovery("online");
});

window.addEventListener("offline", () => {
  logSignalEvent("offline");
});

if (backstageTrigger && backstageDialog && backstagePass) {
  backstageTrigger.addEventListener("click", () => {
    if (typeof masterSecretDialog !== "undefined" && masterSecretDialog) {
      masterSecretDialog.hidden = true;
    }

    if (typeof masterSecretPass !== "undefined" && masterSecretPass) {
      masterSecretPass.value = "";
    }

    backstageDialog.hidden = !backstageDialog.hidden;

    if (!backstageDialog.hidden) {
      backstagePass.focus();
    }
  });
}

if (backstageForm && backstagePass) {
  backstageForm.addEventListener("submit", (event) => {
    event.preventDefault();

    if (backstagePass.value.trim().toLowerCase() === "hoojshwah") {
      unlockBackstage();
    } else {
      backstagePass.value = "";
    }
  });
}


renderSignalTimer();

loadStation();

const donationToggle = document.querySelector("#donation-toggle");
const donationPanel = document.querySelector("#donation-panel");
const barToggle = document.querySelector("#bar-toggle");
const barContent = document.querySelector("#bar-content");
const bottleForm = document.querySelector("#bottle-form");
const bottleStyle = document.querySelector("#bottle-style");
const bottleLabel = document.querySelector("#bottle-label");
const bottleList = document.querySelector("#bottle-list");
const secretGuestbookTrigger = document.querySelector("#secret-guestbook-trigger");
const secretGuestbookDialog = document.querySelector("#secret-guestbook-dialog");
const secretGuestbookForm = document.querySelector("#secret-guestbook-form");
const secretGuestbookPass = document.querySelector("#secret-guestbook-pass");
const reactionStandingsList = document.querySelector("#reaction-standings-list");
const reactionStandingsToggle = document.querySelector("#reaction-standings-toggle");
const masterSecretTrigger = document.querySelector("#master-secret-trigger");
const masterSecretDialog = document.querySelector("#master-secret-dialog");
const masterSecretForm = document.querySelector("#master-secret-form");
const masterSecretPass = document.querySelector("#master-secret-pass");
let masterSignalConfettiTimer = null;
let showAllReactionStandings = false;
let latestReactionStandings = [];

const secretGuestbookItems = [
  ["water-bottle", "Blue Metal Reusable Water Bottle"],
  ["energy-drink", "Energy Drink Can"],
  ["cigarette", "Cigarette"],
  ["mushroom", "Mushroom"],
  ["cola-two-liter", "Two-Liter of Cola"],
  ["coffee-mug", "Coffee Mug"],
  ["skinny-can", "Skinny Can"],
  ["cigar", "Cigar"],
  ["egg-salad-sandwich", "Egg Salad Sandwich"]
];

function unlockSecretGuestbookItems() {
  if (!bottleStyle) {
    return;
  }

  const existingValues = new Set(
    Array.from(bottleStyle.options).map((option) => option.value)
  );

  secretGuestbookItems.forEach(([value, label]) => {
    if (existingValues.has(value)) {
      return;
    }

    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    bottleStyle.appendChild(option);
  });

  bottleStyle.value = "water-bottle";
}

function renderReactionStandingsList() {
  if (!reactionStandingsList) {
    return;
  }

  const visibleStandings = showAllReactionStandings
    ? latestReactionStandings
    : latestReactionStandings.slice(0, 5);

  reactionStandingsList.innerHTML = "";

  if (visibleStandings.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No listener reactions yet.";
    reactionStandingsList.appendChild(item);
    if (reactionStandingsToggle) {
      reactionStandingsToggle.hidden = true;
    }
    return;
  }

  visibleStandings.forEach((entry) => {
    const item = document.createElement("li");
    item.textContent = `${entry.title} — ${entry.total}`;
    reactionStandingsList.appendChild(item);
  });

  if (reactionStandingsToggle) {
    reactionStandingsToggle.hidden = latestReactionStandings.length <= 5;
    reactionStandingsToggle.textContent = showAllReactionStandings ? "Show Top 5" : "Show All";
  }
}

function renderReactionStandingsFromData(reactions) {
  if (!reactionStandingsList || !station || !station.tracks) {
    return;
  }

  const titleSource = station.track_catalog || station.tracks || [];

  const trackNames = new Map(
    titleSource.map((track) => [track.id, track.title])
  );

  latestReactionStandings = Object.entries(reactions)
    .map(([trackId, counts]) => {
      const total = Object.values(counts || {}).reduce((sum, count) => sum + Number(count || 0), 0);
      return {
        title: trackNames.get(trackId) || trackId,
        total
      };
    })
    .filter((entry) => entry.total > 0)
    .sort((a, b) => b.total - a.total);

  renderReactionStandingsList();
}

async function renderReactionStandings() {
  const reactions = await loadReactions();
  renderReactionStandingsFromData(reactions);
}

if (reactionStandingsToggle) {
  reactionStandingsToggle.addEventListener("click", () => {
    showAllReactionStandings = !showAllReactionStandings;
    renderReactionStandingsList();
  });
}


function getBottleStamp(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");

  return `BOTTLED:${year}${month}${day}`;
}

async function loadBottles() {
  try {
    const response = await fetch("/api/bottles", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Bottle API returned ${response.status}`);
    }

    const data = await response.json();
    return data.bottles || [];
  } catch (error) {
    console.error("Could not load bottles:", error);
    return [];
  }
}

async function saveBottle(bottle) {
  const response = await fetch("/api/bottles", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(bottle)
  });

  if (!response.ok) {
    throw new Error(`Bottle API returned ${response.status}`);
  }

  return response.json();
}

async function renderBottles() {
  if (!bottleList) {
    return;
  }

  const bottles = await loadBottles();
  bottleList.innerHTML = "";

  if (bottles.length === 0) {
    const empty = document.createElement("li");
    empty.className = "bottle";
    empty.dataset.stamp = getBottleStamp();
    empty.innerHTML = `
      <div class="bottle-shape green"></div>
      <div class="bottle-label">Signal Jar</div>
      <div class="bottle-hover-date">${empty.dataset.stamp}</div>
    `;
    bottleList.appendChild(empty);
    return;
  }

  bottles.slice().reverse().forEach((bottle) => {
    const item = document.createElement("li");
    item.className = "bottle";
    item.dataset.stamp = bottle.stamp;
    item.innerHTML = `
      <div class="bottle-shape ${bottle.style}"></div>
      <div class="bottle-label"></div>
      <div class="bottle-hover-date"></div>
    `;

    item.querySelector(".bottle-label").textContent = bottle.label;
    item.querySelector(".bottle-hover-date").textContent = item.dataset.stamp;

    bottleList.appendChild(item);
  });
}

if (donationToggle && donationPanel) {
  donationToggle.addEventListener("click", () => {
    donationPanel.hidden = !donationPanel.hidden;
  });
}

if (barToggle && barContent) {
  barToggle.addEventListener("click", () => {
    const isCollapsed = barContent.classList.toggle("collapsed");
    barToggle.textContent = isCollapsed ? "Show Bar" : "Hide Bar";
  });
}

if (secretGuestbookTrigger && secretGuestbookDialog && secretGuestbookForm && secretGuestbookPass) {
  secretGuestbookTrigger.addEventListener("click", () => {
    secretGuestbookDialog.hidden = !secretGuestbookDialog.hidden;

    if (!secretGuestbookDialog.hidden) {
      secretGuestbookPass.focus();
    }
  });

  secretGuestbookForm.addEventListener("submit", (event) => {
    event.preventDefault();

    if (secretGuestbookPass.value.trim().toLowerCase() === "khjw") {
      unlockSecretGuestbookItems();
      secretGuestbookPass.value = "";
      secretGuestbookDialog.hidden = true;
    }
  });
}

if (bottleForm && bottleStyle && bottleLabel) {
  bottleForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const label = bottleLabel.value.trim().slice(0, 16);

    if (!label) {
      return;
    }

    try {
      await saveBottle({
        style: bottleStyle.value,
        label
      });

      bottleLabel.value = "";
      await renderBottles();
      await renderReactionStandings();
    } catch (error) {
      console.error("Could not save bottle:", error);
    }
  });
}

renderBottles();

setInterval(() => {
  renderBottles();
}, 15000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    renderBottles();
  }
});


function fireMasterSignalConfetti() {
  const burst = document.createElement("div");
  burst.className = "master-signal-confetti";
  burst.setAttribute("aria-hidden", "true");

  const pieces = ["✦", "✧", "★", "✶", "◆", "●", "▲", "■"];

  for (let i = 0; i < 42; i += 1) {
    const piece = document.createElement("span");
    piece.textContent = pieces[i % pieces.length];
    piece.style.left = `${Math.random() * 100}%`;
    piece.style.animationDelay = `${Math.random() * 0.45}s`;
    piece.style.transform = `rotate(${Math.random() * 360}deg)`;
    burst.appendChild(piece);
  }

  document.body.appendChild(burst);

  window.setTimeout(() => {
    burst.remove();
  }, 2200);
}

function unlockMasterSignal() {
  backstageUnlocked = true;
  document.body.classList.add("backstage-active");
  renderTrackList(station?.tracks || [], station?.tracks?.[currentTrackIndex]?.id);

  unlockSecretGuestbookItems();

  window.dispatchEvent(new CustomEvent("khjw:arcade-boost"));

  document.body.classList.add("master-signal-active");

  if (masterSecretDialog) {
    masterSecretDialog.hidden = true;
  }

  if (masterSecretPass) {
    masterSecretPass.value = "";
  }

  fireMasterSignalConfetti();

  if (!masterSignalConfettiTimer) {
    masterSignalConfettiTimer = window.setInterval(fireMasterSignalConfetti, 5000);
  }
}

if (masterSecretTrigger && masterSecretDialog && masterSecretPass) {
  masterSecretTrigger.addEventListener("click", () => {
    if (backstageDialog) {
      backstageDialog.hidden = true;
    }

    if (backstagePass) {
      backstagePass.value = "";
    }

    masterSecretDialog.hidden = !masterSecretDialog.hidden;

    if (!masterSecretDialog.hidden) {
      masterSecretPass.focus();
    }
  });
}

if (masterSecretForm && masterSecretPass) {
  masterSecretForm.addEventListener("submit", (event) => {
    event.preventDefault();

    if (masterSecretPass.value.trim().toLowerCase() === "geph") {
      unlockMasterSignal();
    } else {
      masterSecretPass.value = "";
    }
  });
}
