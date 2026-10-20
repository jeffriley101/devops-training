/* Tool configuration consumes the existing mode authority; never writes it. */
(function (root) {
  'use strict';
  const modes = ['Beginner', 'Challenge', 'Performance'];
  function tunerSettings(mode) {
    return mode === 'Beginner' ? {targetWindow: 5, history: 7, stableFrames: 3}
      : {targetWindow: 2, history: 5, stableFrames: 2};
  }
  function metronomeSettings(value = {}) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) value = {};
    return {subdivision: [1, 2, 3].includes(Number(value.subdivision)) ? Number(value.subdivision) : 1,
      beats: [2, 3, 4, 6].includes(Number(value.beats)) ? Number(value.beats) : 4,
      sound: ['Click', 'Clave', 'Snap'].includes(value.sound) ? value.sound : 'Click'};
  }
  function tickSettings(tick, settings) {
    const part = tick % settings.subdivision;
    const beat = Math.floor(tick / settings.subdivision) % settings.beats;
    return {beat, onBeat: part === 0, accent: part === 0 && beat === 0};
  }
  const guest = root.document?.body?.dataset?.guest === 'local';
  let identity = null, mode = null, preferences = {};
  function source() {
    if (root.WWSessionBoundary && !root.WWSessionBoundary.isCurrent()) return {identity: null, mode: null};
    if (guest) return {identity: 'guest', mode: root.WWGuest?.getGameMode() ?? null};
    const state = root.WWState?.getState();
    if (!state?.account?.authenticated || !state.account.woodchuckId ||
        (root.WWSessionBoundary && state.account.woodchuckId !== root.WWSessionBoundary.accountId())) {
      return {identity: null, mode: null};
    }
    return {identity: state.account.woodchuckId, mode: state.profile.game_mode};
  }
  function refresh() {
    const next = source();
    const selected = modes.includes(next.mode) ? next.mode : null;
    const changed = next.identity !== identity || selected !== mode;
    if (next.identity !== identity) preferences = {};
    identity = next.identity; mode = selected;
    if (changed) root.dispatchEvent(new CustomEvent('woodshed:mode'));
  }
  root.WWPracticeTools = Object.freeze({enabled: true, tunerSettings, metronomeSettings, tickSettings,
    getMode: () => mode,
    currentTuner: () => tunerSettings(mode),
    currentMetronome: () => metronomeSettings(preferences[mode]),
    advanced: () => ['Challenge', 'Performance'].includes(mode),
    saveMetronome(value) {
      // Settings are per-mode, in-memory controls, never mode authority or a
      // new persistent preference system. Guest cannot read account caches.
      refresh();
      if (!identity || !mode) return;
      preferences[mode] = metronomeSettings(value);
    },
  });
  root.addEventListener('ww:game-mode-projected', refresh);
  root.addEventListener('ww:guest-setup', refresh);
  root.addEventListener('ww:guest-discarded', () => { preferences = {}; refresh(); });
  root.addEventListener('ww:session-changed', () => { preferences = {}; refresh(); });
  refresh();
})(typeof window !== 'undefined' ? window : globalThis);
