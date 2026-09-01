(function () {
  "use strict";

  const SOUNDTRACKS = Object.freeze({
    "plunge-burrow": {
      url: "/static/audio/arcade/jeremy-9.mp3?v=1",
      restartDelayMs: 6000,
    },
    blue: {
      url: "/static/audio/arcade/gerry-3.mp3?v=1",
      loop: true,
    },
    "radio-tuner": {
      url: "/static/audio/arcade/trouble.mp3?v=1",
      restartDelayMs: 6000,
    },
    "scale-keyboard": {
      url: "/static/audio/arcade/gerry-4.wav?v=1",
      loop: true,
    },
    thirds: {
      url: "/static/audio/arcade/gerry-4.wav?v=1",
      loop: true,
    },
    "dressed-to-the-nines": {
      url: "/static/audio/arcade/sand-drop.mp3?v=1",
      loop: true,
    },
    "wheel-of-woodchuck": {
      url: "/static/audio/arcade/mudslide.mp3?v=1",
      loop: true,
    },
    "interval-basic-training": {
      url: "/static/audio/arcade/black-hole-rappelling.mp3?v=1",
      loop: true,
    },
    "history-mystery": {
      url: "/static/audio/arcade/thunderpants.mp3?v=1",
      loop: true,
    },
  });
  const RESTART_DELAY_MS = 6000;
  const soundtrackRoot = document.querySelector("[data-arcade-soundtrack]");
  const soundtrack = soundtrackRoot && SOUNDTRACKS[soundtrackRoot.dataset.arcadeSoundtrack];
  if (!soundtrack) return;

  const gameKey = soundtrackRoot.dataset.arcadeSoundtrack;
  const audio = new Audio(soundtrack.url);
  let restartTimer = null;
  let resumeTimer = null;
  let stopped = false;
  let runActive = false;
  let playbackActivated = false;

  audio.preload = "auto";
  audio.loop = soundtrack.loop === true;

  function preferences() {
    const sound = window.WoodshedAudio;
    return {
      enabled: !sound || typeof sound.isEnabled !== "function" || sound.isEnabled(),
      volume: sound && typeof sound.getVolume === "function" ? sound.getVolume() : 0.35,
    };
  }

  function applyPreferences() {
    const { enabled, volume } = preferences();
    audio.muted = !enabled;
    audio.volume = Math.max(0, Math.min(1, Number(volume) || 0));
    return enabled;
  }

  function updateSoundtrackToggle() {
    const toggle = document.querySelector("[data-arcade-soundtrack-toggle]");
    if (!toggle) return;
    const musicOn = preferences().enabled && playbackActivated;
    const label = musicOn ? "Mute Arcade soundtrack" : "Turn on Arcade music";
    toggle.textContent = musicOn ? "🔊" : "🔇";
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
  }

  function clearRestart() {
    if (restartTimer !== null) window.clearTimeout(restartTimer);
    restartTimer = null;
  }

  function clearResume() {
    if (resumeTimer !== null) window.clearTimeout(resumeTimer);
    resumeTimer = null;
  }

  function playSoundtrack() {
    if (stopped || runActive || !applyPreferences()) {
      updateSoundtrackToggle();
      return Promise.resolve(false);
    }
    try {
      const attempt = audio.play();
      if (attempt && typeof attempt.then === "function") {
        return attempt.then(function () {
          playbackActivated = true;
          updateSoundtrackToggle();
          return true;
        }).catch(function () {
          playbackActivated = false;
          updateSoundtrackToggle();
          return false;
        });
      }
      playbackActivated = !audio.paused;
      updateSoundtrackToggle();
      return Promise.resolve(playbackActivated);
    } catch (_error) {
      playbackActivated = false;
      updateSoundtrackToggle();
      return Promise.resolve(false);
    }
  }

  function restartAfterPause() {
    clearRestart();
    audio.currentTime = 0;
    restartTimer = window.setTimeout(function () {
      restartTimer = null;
      audio.currentTime = 0;
      playSoundtrack();
    }, RESTART_DELAY_MS);
  }

  function stopSoundtrack() {
    stopped = true;
    clearRestart();
    clearResume();
    audio.pause();
    audio.currentTime = 0;
  }

  function syncSoundtrackPreference() {
    const enabled = applyPreferences();
    if (enabled && !runActive && audio.paused && !audio.ended) void playSoundtrack();
    if (!enabled || runActive) audio.pause();
    updateSoundtrackToggle();
  }

  function activateFromGesture(event) {
    const target = event && event.target;
    if (target && typeof target.closest === "function" && target.closest("[data-arcade-soundtrack-toggle]")) {
      return;
    }
    if (!runActive && audio.paused && !audio.ended) void playSoundtrack();
  }

  function handleRunState(event) {
    const detail = event && event.detail;
    if (!detail || detail.gameKey !== gameKey) return;
    clearResume();
    runActive = detail.active === true;
    if (runActive) {
      clearRestart();
      audio.pause();
      return;
    }
    const delayMs = Math.max(0, Number(detail.resumeDelayMs) || 0);
    resumeTimer = window.setTimeout(function () {
      resumeTimer = null;
      syncSoundtrackPreference();
    }, delayMs);
  }

  if (!audio.loop) audio.addEventListener("ended", restartAfterPause);
  document.addEventListener("pointerdown", activateFromGesture, true);
  document.addEventListener("keydown", activateFromGesture, true);
  document.addEventListener("woodshed:arcade-soundtrack-run-state", handleRunState);
  window.addEventListener("pagehide", stopSoundtrack, { once: true });

  document.addEventListener("DOMContentLoaded", function () {
    const toggle = document.getElementById("sound-effects-enabled");
    const volume = document.getElementById("sound-effects-volume");
    const soundtrackToggle = document.querySelector("[data-arcade-soundtrack-toggle]");
    if (toggle) toggle.addEventListener("change", syncSoundtrackPreference);
    if (volume) volume.addEventListener("input", syncSoundtrackPreference);
    if (soundtrackToggle) {
      soundtrackToggle.addEventListener("click", async function () {
        if (!window.WoodshedAudio || typeof window.WoodshedAudio.setEnabled !== "function") return;
        if (preferences().enabled && playbackActivated) {
          window.WoodshedAudio.setEnabled(false);
          syncSoundtrackPreference();
          return;
        }
        if (!preferences().enabled) window.WoodshedAudio.setEnabled(true);
        applyPreferences();
        await playSoundtrack();
        updateSoundtrackToggle();
      });
    }
    applyPreferences();
    updateSoundtrackToggle();
    audio.currentTime = 0;
  }, { once: true });
}());
