(function () {
  "use strict";

  const STORAGE_ENABLED = "woodshed.soundEffects.enabled";
  const STORAGE_VOLUME = "woodshed.soundEffects.volume";
  const DEFAULT_VOLUME = 0.35;
  const EFFECT_NAMES = [
    "correctTrivia", "incorrectTrivia", "dandelionEarned",
    "campPointEarned", "pChartSubmitted", "crownEarned", "dialClick",
    "secretReward", "goatTracker", "questCompleted", "bandCampBonus",
    "marchingCompleted", "practiceRoomOpen", "medalEarned",
    "burrowPortal", "carrotCollected", "instrumentCollected",
    "bandSetCompleted", "arcadePickup", "arcadeCheer",
  ];
  const GOAT_CLIP_URLS = [
    "/static/audio/goats/goat-01.mp3",
    "/static/audio/goats/goat-02.mp3",
    "/static/audio/goats/goat-03.mp3",
    "/static/audio/goats/goat-04.mp3",
    "/static/audio/goats/goat-05.mp3",
  ];
  const EXPECTED_GOAT_CLIP_URLS = [
    "/static/audio/goats/goat-01.mp3",
    "/static/audio/goats/goat-02.mp3",
    "/static/audio/goats/goat-03.mp3",
    "/static/audio/goats/goat-04.mp3",
    "/static/audio/goats/goat-05.mp3",
  ];
  let enabled = readBoolean(STORAGE_ENABLED, true);
  let volume = readVolume();
  let unlocked = false;
  let unlockPending = null;
  let graph = null;
  let crownUntil = 0;
  let goatPlayers = [];
  let goatPoolLoading = false;
  let goatPlayPending = false;
  let lastGoatIndex = -1;
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
    const flourish = new Tone.PolySynth(Tone.Synth, {
      maxPolyphony: 6,
      options: {
        oscillator: { type: "triangle8" },
        envelope: { attack: 0.006, decay: 0.16, sustain: 0.08, release: 0.45 },
        volume: -18,
      },
    }).connect(filter);
    const piano = new Tone.PolySynth(Tone.Synth, {
      maxPolyphony: 10,
      options: {
        oscillator: { type: "triangle8" },
        envelope: { attack: 0.004, decay: 0.3, sustain: 0.025, release: 0.18 },
        volume: -17,
      },
    }).connect(filter);
    const doorFilter = new Tone.Filter({ frequency: 380, type: "bandpass", Q: 1.2 }).connect(master);
    const door = new Tone.NoiseSynth({
      noise: { type: "brown" },
      envelope: { attack: 0.08, decay: 0.38, sustain: 0, release: 0.18 },
      volume: -28,
    }).connect(doorFilter);
    const crowdFilter = new Tone.Filter({
      frequency: 1100, type: "bandpass", Q: 0.7,
    }).connect(master);
    const crowd = new Tone.NoiseSynth({
      noise: { type: "pink" },
      envelope: { attack: 0.04, decay: 0.62, sustain: 0, release: 0.16 },
      volume: -27,
    }).connect(crowdFilter);
    const goatGain = new Tone.Gain(0.35).connect(master);
    graph = {
      master, filter, chime, warm, wood, click, flourish, piano,
      doorFilter, door, crowdFilter, crowd, goatGain,
    };
    buildGoatPool(Tone, goatGain);
    return graph;
  }

  function isLocalGoatUrl(url) {
    return typeof url === "string" &&
      /^\/static\/audio\/goats\/[a-z0-9][a-z0-9._-]*\.(mp3|ogg|wav)$/i.test(url);
  }

  function buildGoatPool(Tone, output) {
    if (goatPlayers.length || goatPoolLoading || !GOAT_CLIP_URLS.length) return;
    goatPoolLoading = true;
    const loads = GOAT_CLIP_URLS.filter(isLocalGoatUrl).map(async function (url) {
      try {
        const response = await window.fetch(url, { credentials: "same-origin" });
        if (!response.ok) return null;
        const decoded = await Tone.getContext().decodeAudioData(await response.arrayBuffer());
        const player = new Tone.Player({ url: decoded, autostart: false, loop: false });
        player.connect(output);
        return player;
      } catch (_error) {
        return null;
      }
    });
    Promise.all(loads).then(function (players) {
      goatPlayers = players.filter(Boolean);
      if (goatPlayPending) {
        goatPlayPending = false;
        if (enabled && Date.now() >= crownUntil) playGoat();
      }
    }).catch(function () {}).finally(function () {
      goatPoolLoading = false;
    });
  }

  function chooseGoatIndex(length, randomValue) {
    if (length < 1) return -1;
    let index = Math.min(length - 1, Math.floor(randomValue * length));
    if (length > 1 && index === lastGoatIndex) index = (index + 1) % length;
    return index;
  }

  function playGoat() {
    const readyPlayers = goatPlayers.filter(function (player) {
      return player && player.loaded === true;
    });
    if (!readyPlayers.length) {
      if (goatPoolLoading) goatPlayPending = true;
      return false;
    }
    const index = chooseGoatIndex(readyPlayers.length, Math.random());
    if (index < 0) return false;
    goatPlayers.forEach(function (player) {
      try { if (player.state === "started") player.stop(); } catch (_error) {}
    });
    try {
      readyPlayers[index].start();
      lastGoatIndex = index;
      return true;
    } catch (_error) {
      return false;
    }
  }

  function applyOutputLevel() {
    if (!graph) return;
    try { graph.master.gain.rampTo(outputLevel(), 0.02); } catch (_error) {
      graph.master.gain.value = outputLevel();
    }
  }

  function toneAudioContext() {
    if (!window.Tone || typeof window.Tone.getContext !== "function") return null;
    try {
      const context = window.Tone.getContext();
      return context && context.rawContext ? context.rawContext : context;
    } catch (_error) {
      return null;
    }
  }

  function audioContextIsRunning() {
    const context = toneAudioContext();
    return Boolean(
      context &&
      (typeof context.state !== "string" || context.state === "running")
    );
  }

  function primeAudioContextFromGesture() {
    const context = toneAudioContext();
    if (!context) return;

    try {
      if (context.state !== "running" && typeof context.resume === "function") {
        Promise.resolve(context.resume()).then(function () {
          if (!audioContextIsRunning()) return;
          unlocked = true;
          buildGraph();
        }).catch(function () {});
      }

      if (
        typeof context.createBuffer === "function" &&
        typeof context.createBufferSource === "function" &&
        context.destination
      ) {
        const source = context.createBufferSource();
        source.buffer = context.createBuffer(1, 1, context.sampleRate || 22050);
        source.connect(context.destination);
        source.onended = function () {
          try { source.disconnect(); } catch (_error) {}
        };
        source.start(0);
      }
    } catch (_error) {
      // A later user gesture can try the mobile audio primer again.
    }
  }

  function markAudioReady() {
    if (!audioContextIsRunning()) {
      unlocked = false;
      return false;
    }
    unlocked = true;
    buildGraph();
    return true;
  }

  function unlock() {
    if (unlocked && audioContextIsRunning()) return Promise.resolve(true);
    if (!window.Tone || typeof window.Tone.start !== "function") {
      unlocked = false;
      return Promise.resolve(false);
    }

    primeAudioContextFromGesture();
    if (unlockPending) return unlockPending;
    unlocked = false;
    let startResult;
    try {
      startResult = window.Tone.start();
    } catch (_error) {
      startResult = Promise.reject(_error);
    }
    unlockPending = Promise.resolve(startResult)
      .catch(function () { return false; })
      .then(async function () {
        const context = toneAudioContext();
        if (
          context && context.state !== "running" &&
          typeof context.resume === "function"
        ) {
          try { await context.resume(); } catch (_error) { return false; }
        }
        return markAudioReady();
      })
      .finally(function () { unlockPending = null; });
    return unlockPending;
  }

  function canPlay(name) {
    if (
      !enabled || !unlocked || !audioContextIsRunning() ||
      !graph || !EFFECT_NAMES.includes(name)
    ) return false;
    const nowMs = Date.now();
    if (name !== "crownEarned" && nowMs < crownUntil) return false;
    const quietPeriods = {
      dialClick: 55, goatTracker: 900, questCompleted: 700,
      bandCampBonus: 600, marchingCompleted: 600,
      practiceRoomOpen: 700, medalEarned: 1000,
      burrowPortal: 300, carrotCollected: 350,
      instrumentCollected: 350, bandSetCompleted: 900, arcadePickup: 45,
    };
    const quietPeriod = quietPeriods[name] || 120;
    if (nowMs - (lastPlayed.get(name) || 0) < quietPeriod) return false;
    lastPlayed.set(name, nowMs);
    return true;
  }

  function play(name) {
    if (!enabled || !EFFECT_NAMES.includes(name)) return false;
    if (!unlocked || !audioContextIsRunning() || !graph) {
      unlock().then(function (ready) {
        if (ready) play(name);
      }).catch(function () {});
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
        goatPlayers.forEach(function (player) {
          try { if (player.state === "started") player.stop(); } catch (_error) {}
        });
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
      } else if (name === "goatTracker") {
        return playGoat();
      } else if (name === "questCompleted") {
        graph.wood.triggerAttackRelease("C4", 0.08, now, 0.38);
        graph.wood.triggerAttackRelease("G4", 0.08, now + 0.13, 0.32);
        graph.chime.triggerAttackRelease("C5", 0.3, now + 0.28, 0.38);
        graph.chime.triggerAttackRelease("E5", 0.34, now + 0.4, 0.3);
      } else if (name === "bandCampBonus") {
        [0, 0.09, 0.18, 0.3].forEach(function (offset, index) {
          graph.click.triggerAttackRelease(0.035, now + offset, index === 3 ? 0.26 : 0.18);
        });
        graph.chime.triggerAttackRelease("G4", 0.22, now + 0.4, 0.28);
      } else if (name === "marchingCompleted") {
        [0, 0.15, 0.3].forEach(function (offset, index) {
          graph.wood.triggerAttackRelease(index === 2 ? "D4" : "C4", 0.06, now + offset, 0.3);
        });
        graph.chime.triggerAttackRelease("D5", 0.25, now + 0.43, 0.34);
      } else if (name === "practiceRoomOpen") {
        graph.doorFilter.frequency.setValueAtTime(300, now);
        graph.doorFilter.frequency.exponentialRampToValueAtTime(1250, now + 0.48);
        graph.door.triggerAttackRelease(0.5, now, 0.24);
        graph.chime.triggerAttackRelease("A4", 0.24, now + 0.48, 0.24);
      } else if (name === "medalEarned") {
        [["G4", 0], ["B4", 0.18], ["D5", 0.36]].forEach(function (step) {
          graph.flourish.triggerAttackRelease(step[0], 0.3, now + step[1], 0.36);
        });
        graph.chime.triggerAttackRelease(["G4", "B4", "D5"], 0.42, now + 0.56, 0.28);
      } else if (name === "burrowPortal") {
        graph.doorFilter.frequency.setValueAtTime(520, now);
        graph.doorFilter.frequency.exponentialRampToValueAtTime(1800, now + 0.22);
        graph.door.triggerAttackRelease(0.22, now, 0.15);
        graph.chime.triggerAttackRelease("D5", 0.1, now + 0.2, 0.18);
      } else if (name === "carrotCollected") {
        graph.warm.triggerAttackRelease("G4", 0.14, now, 0.3);
        graph.chime.triggerAttackRelease("C5", 0.18, now + 0.13, 0.25);
      } else if (name === "instrumentCollected") {
        graph.wood.triggerAttackRelease("E4", 0.06, now, 0.28);
        graph.chime.triggerAttackRelease("A4", 0.2, now + 0.08, 0.26);
      } else if (name === "bandSetCompleted") {
        [["C4", 0], ["E4", 0.13], ["G4", 0.26], ["C5", 0.43]].forEach(function (step) {
          graph.flourish.triggerAttackRelease(step[0], 0.24, now + step[1], 0.3);
        });
        graph.chime.triggerAttackRelease(["E5", "G5"], 0.3, now + 0.55, 0.24);
      } else if (name === "arcadePickup") {
        graph.chime.triggerAttackRelease("B5", 0.075, now, 0.2);
      } else if (name === "arcadeCheer") {
        graph.crowdFilter.frequency.setValueAtTime(850, now);
        graph.crowdFilter.frequency.exponentialRampToValueAtTime(2400, now + 0.62);
        graph.crowd.triggerAttackRelease(0.72, now, 0.3);
        [["C5", 0.08], ["E5", 0.2], ["G5", 0.34]].forEach(function (step) {
          graph.chime.triggerAttackRelease(step[0], 0.18, now + step[1], 0.2);
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
      button.textContent = "🎧";
      button.setAttribute("aria-label", `Audio settings. Sound Effects ${enabled ? "On" : "Off"}.`);
      button.title = `Audio settings. Sound Effects ${enabled ? "On" : "Off"}.`;
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

  function isDedicatedMediaGesture(event) {
    const target = event && event.target;
    return Boolean(
      target && typeof target.closest === "function" &&
      target.closest(
        "#tuner-open-button, #tuner-panel, #metronome-open-button, #metronome-panel"
      )
    );
  }

  function gestureUnlock(event) {
    if (event.type === "keydown" && event.isComposing) return;
    if (isDedicatedMediaGesture(event)) return;
    unlock().catch(function () {});
  }

  function refreshAudioState() {
    if (!audioContextIsRunning()) unlocked = false;
  }

  function playPianoPitch(frequency) {
    const pitch = Number(frequency);
    if (!enabled || !Number.isFinite(pitch) || pitch <= 0) return false;
    function trigger() {
      const current = buildGraph();
      if (!current) return false;
      current.piano.triggerAttackRelease(pitch, 0.42);
      return true;
    }
    if (audioContextIsRunning()) return trigger();
    unlock().then(trigger).catch(function () {});
    return true;
  }

  document.addEventListener("pointerdown", gestureUnlock, true);
  document.addEventListener("touchend", gestureUnlock, true);
  document.addEventListener("click", gestureUnlock, true);
  document.addEventListener("keydown", gestureUnlock, true);
  document.addEventListener("visibilitychange", refreshAudioState);
  window.addEventListener("pageshow", refreshAudioState);
  document.addEventListener("DOMContentLoaded", wireControls, { once: true });

  window.WoodshedAudio = {
    unlock, play, playCampReward, playPianoPitch, setEnabled, setVolume,
    isEnabled: function () { return enabled; },
    getVolume: function () { return volume; },
    getContextState: function () {
      const context = toneAudioContext();
      return context && typeof context.state === "string" ? context.state : "unavailable";
    },
    effectNames: EFFECT_NAMES.slice(),
    goatClipUrls: GOAT_CLIP_URLS.slice(),
    expectedGoatClipUrls: EXPECTED_GOAT_CLIP_URLS.slice(),
  };
}());
