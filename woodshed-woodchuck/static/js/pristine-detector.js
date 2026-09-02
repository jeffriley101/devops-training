(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.PristinePracticeDetector = api;
}(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const START_CONFIRMATION_MS = 180;
  const SMOOTHING_ALPHA = 0.35;
  const CALIBRATION_ALPHA = 0.08;
  const IDLE_FLOOR_ALPHA = 0.012;
  const MAX_IDLE_FLOOR_STEP = 0.00015;
  const MIN_START_THRESHOLD = 0.014;
  const MIN_CONTINUE_THRESHOLD = 0.008;
  const START_FLOOR_MULTIPLIER = 2.5;
  const START_FLOOR_OFFSET = 0.006;
  const CONTINUE_FLOOR_MULTIPLIER = 1.35;
  const CONTINUE_FLOOR_OFFSET = 0.003;
  const MIN_STRONG_TRANSIENT = 0.055;
  const STRONG_TRANSIENT_MULTIPLIER = 2.3;

  function bounded(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function createDetector(options) {
    const settings = options || {};
    const confirmationMs = settings.confirmationMs ?? START_CONFIRMATION_MS;
    const smoothingAlpha = settings.smoothingAlpha ?? SMOOTHING_ALPHA;
    const calibrationAlpha = settings.calibrationAlpha ?? CALIBRATION_ALPHA;
    const idleFloorAlpha = settings.idleFloorAlpha ?? IDLE_FLOOR_ALPHA;
    const maxIdleFloorStep = settings.maxIdleFloorStep ?? MAX_IDLE_FLOOR_STEP;
    const minStartThreshold = settings.minStartThreshold ?? MIN_START_THRESHOLD;
    const minContinueThreshold = settings.minContinueThreshold ?? MIN_CONTINUE_THRESHOLD;
    const startFloorMultiplier = settings.startFloorMultiplier ?? START_FLOOR_MULTIPLIER;
    const startFloorOffset = settings.startFloorOffset ?? START_FLOOR_OFFSET;
    const continueFloorMultiplier = settings.continueFloorMultiplier ?? CONTINUE_FLOOR_MULTIPLIER;
    const continueFloorOffset = settings.continueFloorOffset ?? CONTINUE_FLOOR_OFFSET;
    const minStrongTransient = settings.minStrongTransient ?? MIN_STRONG_TRANSIENT;
    const strongTransientMultiplier = settings.strongTransientMultiplier ?? STRONG_TRANSIENT_MULTIPLIER;
    let noiseFloor = Math.max(0.0001, settings.initialNoiseFloor ?? 0.008);
    let smoothedRms = noiseFloor;
    let candidateStartedAt = null;

    function thresholds() {
      const startThreshold = Math.max(
        minStartThreshold, (noiseFloor * startFloorMultiplier) + startFloorOffset
      );
      const continueThreshold = Math.max(
        minContinueThreshold, (noiseFloor * continueFloorMultiplier) + continueFloorOffset
      );
      return {
        startThreshold,
        continueThreshold: Math.min(continueThreshold, startThreshold * 0.82),
        strongTransientThreshold: Math.max(
          minStrongTransient, startThreshold * strongTransientMultiplier
        ),
      };
    }

    function adaptIdleFloor(rms, startThreshold) {
      // Only quiet idle frames are eligible.  A one-off bump cannot leap the
      // baseline because both the target and the per-frame step are bounded.
      if (rms > startThreshold) return;
      const target = Math.min(rms, (noiseFloor * 1.35) + 0.003);
      const proposed = (target - noiseFloor) * idleFloorAlpha;
      noiseFloor = Math.max(
        0.0001,
        noiseFloor + bounded(proposed, -maxIdleFloorStep, maxIdleFloorStep)
      );
    }

    function sample(rms, now, options) {
      if (!Number.isFinite(rms) || rms < 0) {
        throw new TypeError("Pristine detector samples require a non-negative RMS value.");
      }
      if (!Number.isFinite(now)) {
        throw new TypeError("Pristine detector samples require a timestamp.");
      }
      const state = options || {};
      smoothedRms += (rms - smoothedRms) * smoothingAlpha;
      if (state.calibrating) {
        noiseFloor += (rms - noiseFloor) * calibrationAlpha;
        candidateStartedAt = null;
        return snapshot(false, false);
      }

      const values = thresholds();
      if (state.canContinue) {
        candidateStartedAt = null;
        return snapshot(
          smoothedRms >= values.continueThreshold || rms >= values.continueThreshold,
          false
        );
      }

      if (rms >= values.strongTransientThreshold) {
        candidateStartedAt = null;
        return snapshot(true, true);
      }
      if (smoothedRms >= values.startThreshold) {
        if (candidateStartedAt === null) candidateStartedAt = now;
        if (now - candidateStartedAt >= confirmationMs) {
          candidateStartedAt = null;
          return snapshot(true, false);
        }
      } else {
        candidateStartedAt = null;
        adaptIdleFloor(rms, values.startThreshold);
      }
      return snapshot(false, false);
    }

    function snapshot(detected, strongTransient) {
      const values = thresholds();
      return Object.freeze({
        detected,
        strongTransient,
        noiseFloor,
        smoothedRms,
        startThreshold: values.startThreshold,
        continueThreshold: values.continueThreshold,
        confirming: candidateStartedAt !== null,
      });
    }

    return Object.freeze({ sample, snapshot });
  }

  return Object.freeze({
    createDetector,
    START_CONFIRMATION_MS,
    MIN_START_THRESHOLD,
    MIN_CONTINUE_THRESHOLD,
  });
}));
