(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.PristinePracticeTimer = api;
}(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const SILENCE_GRACE_MS = 10000;
  const SAFETY_PAUSE_MS = 60 * 60 * 1000;

  function createTimer(options) {
    const settings = options || {};
    const silenceGraceMs = settings.silenceGraceMs ?? SILENCE_GRACE_MS;
    const safetyPauseMs = settings.safetyPauseMs ?? SAFETY_PAUSE_MS;
    const maxSampleGapMs = settings.maxSampleGapMs ?? 1000;
    let playingMs = 0;
    let lastSampleAt = null;
    let soundDetected = false;
    let hasPlayed = false;
    let silenceStartedAt = null;
    let silencePaused = false;
    let manualPaused = false;
    let safetyPaused = false;
    let safetyCheckpointReached = false;
    let done = false;

    function snapshot() {
      let status = "listening";
      if (done) status = "done";
      else if (safetyPaused) status = "safety-paused";
      else if (manualPaused || silencePaused) status = "paused";
      else if (soundDetected) status = "playing";
      return Object.freeze({
        playingMilliseconds: Math.floor(playingMs),
        playingSeconds: Math.floor(playingMs / 1000),
        soundDetected,
        status,
        paused: manualPaused || silencePaused || safetyPaused,
        safetyPaused,
        done,
        silenceMilliseconds: silenceStartedAt === null || soundDetected
          ? 0
          : Math.max(0, (lastSampleAt ?? silenceStartedAt) - silenceStartedAt),
      });
    }

    function sample(now, nextSoundDetected) {
      if (!Number.isFinite(now)) throw new TypeError("Timer samples require a timestamp.");
      if (lastSampleAt !== null && now < lastSampleAt) {
        throw new RangeError("Timer samples must be chronological.");
      }
      if (done) return snapshot();

      if (lastSampleAt !== null && soundDetected && !manualPaused &&
          !silencePaused && !safetyPaused) {
        playingMs += Math.min(now - lastSampleAt, maxSampleGapMs);
      }
      lastSampleAt = now;
      soundDetected = Boolean(nextSoundDetected);

      if (soundDetected) {
        hasPlayed = true;
        silenceStartedAt = null;
        silencePaused = false;
      } else if (hasPlayed && silenceStartedAt === null) {
        silenceStartedAt = now;
      }
      if (!soundDetected && silenceStartedAt !== null &&
          now - silenceStartedAt >= silenceGraceMs) {
        silencePaused = true;
      }

      if (!safetyCheckpointReached && playingMs >= safetyPauseMs) {
        playingMs = safetyPauseMs;
        safetyPaused = true;
        safetyCheckpointReached = true;
      }
      return snapshot();
    }

    function pause(now) {
      sample(now, soundDetected);
      manualPaused = true;
      return snapshot();
    }

    function resume(now) {
      if (done) return snapshot();
      if (!Number.isFinite(now)) throw new TypeError("Timer resume requires a timestamp.");
      manualPaused = false;
      safetyPaused = false;
      silencePaused = false;
      silenceStartedAt = soundDetected || !hasPlayed ? null : now;
      lastSampleAt = now;
      return snapshot();
    }

    function finish(now) {
      sample(now, soundDetected);
      done = true;
      soundDetected = false;
      return snapshot();
    }

    return Object.freeze({ finish, pause, resume, sample, snapshot });
  }

  return Object.freeze({ createTimer, SAFETY_PAUSE_MS, SILENCE_GRACE_MS });
}));
