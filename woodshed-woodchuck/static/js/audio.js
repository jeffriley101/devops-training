(function () {
  "use strict";

  const STORAGE_ENABLED = "woodshed.soundEffects.enabled";
  const STORAGE_VOLUME = "woodshed.soundEffects.volume";
  const DEFAULT_VOLUME = 0.35;
  const EFFECT_NAMES = [
    "correctTrivia", "incorrectTrivia", "dandelionEarned",
    "campPointEarned", "pChartSubmitted", "crownEarned", "dialClick",
    "secretReward",
  ];
  let enabled = readBoolean(STORAGE_ENABLED, true);
  let volume = readVolume();
  let unlocked = false;
  let unlockPending = null;
  let graph = null;
  let crownUntil = 0;
  const lastPlayed = new Map();

  function readBoolean(key, fallback) {
    try {
      const stored = window.localStorage.getItem(key);
      return stored === null ? fallback : stored === "true";
    } catch (_error) {
      return fallback;
    }
  }

  function readVolume() {
    try {
      const raw = window.localStorage.getItem(STORAGE_VOLUME);
      if (raw === null) return DEFAULT_VOLUME;
      const stored = Number(raw);
      return Number.isFinite(stored) && stored >= 0 && stored <= 1
        ? stored : DEFAULT_VOLUME;
    } catch (_error) {
      return DEFAULT_VOLUME;
    }
  }

  function persist(key, value) {
    try { window.localStorage.setItem(key, String(value)); } catch (_error) {
      // Preferences remain usable for this page when storage is unavailable.
    }
  }

  function outputLevel() { return enabled ? volume : 0; }

  function buildGraph() {
    if (graph || !window.Tone) return graph;
    const Tone = window.Tone;
    const master = new Tone.Gain(outputLevel()).toDestination();
    const filter = new Tone.Filter({ frequency: 4200, type: "lowpass", rolloff: -12 }).connect(master);
    const chime = new Tone.PolySynth(Tone.Synth, {
      maxPolyphony: 6,
      options: {
        oscillator: { type: "sine" },
        envelope: { attack: 0.008, decay: 0.13, sustain: 0.12, release: 0.38 },
        volume: -13,
      },
    }).connect(filter);
    const warm = new Tone.PolySynth(Tone.Synth, {
      maxPolyphony: 5,
      options: {
        oscillator: { type: "triangle" },
        envelope: { attack: 0.012, decay: 0.12, sustain: 0.1, release: 0.32 },
        volume: -15,
      },
    }).connect(filter);
    const wood = new Tone.MembraneSynth({
      pitchDecay: 0.018, octaves: 1.5,
      oscillator: { type: "sine" },
      envelope: { attack: 0.001, decay: 0.09, sustain: 0, release: 0.08 },
      volume: -20,
    }).connect(filter);
    const click = new Tone.NoiseSynth({
      noise: { type: "brown" },
      envelope: { attack: 0.001, decay: 0.025, sustain: 0, release: 0.01 },
      volume: -29,
    }).connect(filter);
    graph = { master, filter, chime, warm, wood, click };
    return graph;
  }

  function applyOutputLevel() {
    if (!graph) return;
    try { graph.master.gain.rampTo(outputLevel(), 0.02); } catch (_error) {
      graph.master.gain.value = outputLevel();
    }
  }

  function unlock() {
    if (unlocked) return Promise.resolve(true);
    if (unlockPending) return unlockPending;
    if (!window.Tone || typeof window.Tone.start !== "function") {
      return Promise.resolve(false);
    }
    unlockPending = Promise.resolve(window.Tone.start())
      .then(function () {
        unlocked = true;
        buildGraph();
        return true;
      })
      .catch(function () { return false; })
      .finally(function () { unlockPending = null; });
    return unlockPending;
  }

  function canPlay(name) {
    if (!enabled || !unlocked || !graph || !EFFECT_NAMES.includes(name)) return false;
    const nowMs = Date.now();
    if (name !== "crownEarned" && nowMs < crownUntil) return false;
    const quietPeriod = name === "dialClick" ? 55 : 120;
    if (nowMs - (lastPlayed.get(name) || 0) < quietPeriod) return false;
    lastPlayed.set(name, nowMs);
    return true;
  }

  function play(name) {
    if (!unlocked && unlockPending && enabled) {
      unlockPending.then(function (ready) { if (ready) play(name); }).catch(function () {});
      return false;
    }
    if (!canPlay(name)) return false;
    try {
      const now = window.Tone.now() + 0.015;
      if (name === "correctTrivia") {
        graph.chime.triggerAttackRelease("C5", 0.16, now, 0.48);
        graph.chime.triggerAttackRelease("E5", 0.24, now + 0.16, 0.42);
      } else if (name === "incorrectTrivia") {
        graph.warm.triggerAttackRelease("D4", 0.2, now, 0.38);
        graph.warm.triggerAttackRelease("A3", 0.24, now + 0.18, 0.34);
      } else if (name === "dandelionEarned") {
        ["E5", "G5", "B5"].forEach(function (note, index) {
          graph.chime.triggerAttackRelease(note, 0.11, now + index * 0.1, 0.27);
        });
      } else if (name === "campPointEarned") {
        graph.wood.triggerAttackRelease("C4", 0.07, now, 0.42);
        graph.chime.triggerAttackRelease("G4", 0.16, now + 0.07, 0.3);
      } else if (name === "pChartSubmitted") {
        ["C4", "E4", "G4"].forEach(function (note, index) {
          graph.warm.triggerAttackRelease(note, 0.2, now + index * 0.13, 0.32);
        });
      } else if (name === "crownEarned") {
        crownUntil = Date.now() + 1800;
        [["C4", 0], ["E4", 0.16], ["G4", 0.32], ["C5", 0.52]].forEach(function (step) {
          graph.chime.triggerAttackRelease(step[0], 0.3, now + step[1], 0.42);
          if (step[1] >= 0.32) graph.warm.triggerAttackRelease(step[0], 0.38, now + step[1], 0.22);
        });
      } else if (name === "dialClick") {
        graph.click.triggerAttackRelease(0.025, now, 0.18);
      } else if (name === "secretReward") {
        ["A3", "D4", "F4"].forEach(function (note, index) {
          graph.warm.triggerAttackRelease(note, 0.18, now + index * 0.14, 0.3);
        });
      }
      return true;
    } catch (_error) {
      return false;
    }
  }

  function playCampReward(includeTriviaChime) {
    if (includeTriviaChime) play("correctTrivia");
    window.setTimeout(function () { play("campPointEarned"); }, includeTriviaChime ? 210 : 0);
    window.setTimeout(function () { play("dandelionEarned"); }, includeTriviaChime ? 430 : 190);
  }

  function setEnabled(value) {
    enabled = Boolean(value);
    persist(STORAGE_ENABLED, enabled);
    applyOutputLevel();
    updateControls();
  }

  function setVolume(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    volume = Math.min(1, Math.max(0, parsed));
    persist(STORAGE_VOLUME, volume);
    applyOutputLevel();
    updateControls();
  }

  function updateControls() {
    const button = document.getElementById("sound-effects-button");
    const toggle = document.getElementById("sound-effects-enabled");
    const slider = document.getElementById("sound-effects-volume");
    const value = document.getElementById("sound-effects-volume-value");
    if (button) {
      button.textContent = enabled ? "🔊" : "🔇";
      button.setAttribute("aria-label", `Sound Effects ${enabled ? "On" : "Off"}. Open settings.`);
    }
    if (toggle) {
      toggle.checked = enabled;
      toggle.setAttribute("aria-label", `Sound Effects ${enabled ? "On" : "Off"}`);
    }
    if (slider) {
      slider.value = String(Math.round(volume * 100));
      slider.setAttribute("aria-valuetext", `${Math.round(volume * 100)} percent`);
    }
    if (value) value.textContent = `${Math.round(volume * 100)}%`;
  }

  function wireControls() {
    const button = document.getElementById("sound-effects-button");
    const panel = document.getElementById("sound-effects-panel");
    const toggle = document.getElementById("sound-effects-enabled");
    const slider = document.getElementById("sound-effects-volume");
    if (!button || !panel || !toggle || !slider) return;
    updateControls();
    button.addEventListener("click", function () {
      const opening = panel.hidden;
      panel.hidden = !opening;
      button.setAttribute("aria-expanded", String(opening));
      if (opening) toggle.focus();
    });
    toggle.addEventListener("change", function () { setEnabled(toggle.checked); });
    slider.addEventListener("input", function () { setVolume(Number(slider.value) / 100); });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !panel.hidden) {
        panel.hidden = true;
        button.setAttribute("aria-expanded", "false");
        button.focus();
      }
    });
  }

  function gestureUnlock(event) {
    if (event.type === "keydown" && event.isComposing) return;
    unlock().then(function (ready) {
      if (!ready) return;
      document.removeEventListener("pointerdown", gestureUnlock, true);
      document.removeEventListener("click", gestureUnlock, true);
      document.removeEventListener("keydown", gestureUnlock, true);
    }).catch(function () {});
  }
  document.addEventListener("pointerdown", gestureUnlock, true);
  document.addEventListener("click", gestureUnlock, true);
  document.addEventListener("keydown", gestureUnlock, true);
  document.addEventListener("DOMContentLoaded", wireControls, { once: true });

  window.WoodshedAudio = {
    unlock, play, playCampReward, setEnabled, setVolume,
    isEnabled: function () { return enabled; },
    getVolume: function () { return volume; },
    effectNames: EFFECT_NAMES.slice(),
  };
}());
