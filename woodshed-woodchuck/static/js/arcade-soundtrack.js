(function () {
  "use strict";

  const SOUNDTRACK_URL = "/static/audio/arcade/trouble.mp3?v=1";
  const RESTART_DELAY_MS = 6000;
  const audio = new Audio(SOUNDTRACK_URL);
  let restartTimer = null;
  let stopped = false;

  audio.preload = "auto";

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
    const enabled = preferences().enabled;
    toggle.textContent = enabled ? "🔊" : "🔇";
    toggle.setAttribute("aria-label", `${enabled ? "Mute" : "Unmute"} Arcade soundtrack`);
    toggle.setAttribute("title", `${enabled ? "Mute" : "Unmute"} Arcade soundtrack`);
  }

  function clearRestart() {
    if (restartTimer !== null) window.clearTimeout(restartTimer);
    restartTimer = null;
  }

  function playSoundtrack() {
    if (stopped || !applyPreferences()) return;
    const attempt = audio.play();
    if (attempt && typeof attempt.catch === "function") {
      attempt.catch(function () {
        // A later normal user gesture can satisfy browser autoplay policy.
      });
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
    audio.pause();
    audio.currentTime = 0;
  }

  function syncSoundtrackPreference() {
    const enabled = applyPreferences();
    if (enabled && audio.paused && !audio.ended) playSoundtrack();
    if (!enabled) audio.pause();
    updateSoundtrackToggle();
  }

  function activateFromGesture() {
    if (audio.paused && !audio.ended) playSoundtrack();
  }

  audio.addEventListener("ended", restartAfterPause);
  document.addEventListener("pointerdown", activateFromGesture, true);
  document.addEventListener("keydown", activateFromGesture, true);
  window.addEventListener("pagehide", stopSoundtrack, { once: true });

  document.addEventListener("DOMContentLoaded", function () {
    const toggle = document.getElementById("sound-effects-enabled");
    const volume = document.getElementById("sound-effects-volume");
    const soundtrackToggle = document.querySelector("[data-arcade-soundtrack-toggle]");
    if (toggle) toggle.addEventListener("change", syncSoundtrackPreference);
    if (volume) volume.addEventListener("input", syncSoundtrackPreference);
    if (soundtrackToggle) {
      soundtrackToggle.addEventListener("click", function () {
        if (!window.WoodshedAudio || typeof window.WoodshedAudio.setEnabled !== "function") return;
        window.WoodshedAudio.setEnabled(!preferences().enabled);
        syncSoundtrackPreference();
      });
    }
    applyPreferences();
    updateSoundtrackToggle();
    audio.currentTime = 0;
    playSoundtrack();
  }, { once: true });
}());
