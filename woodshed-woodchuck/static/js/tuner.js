(function (root) {
  "use strict";

  const NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

  function classifyCents(cents) {
    const value = Number(cents);
    if (!Number.isFinite(value)) return "NEUTRAL";
    const distance = Math.abs(value);
    if (distance <= 0.5) return "PRISTINE";
    if (distance <= 3) return "GOOD";
    if (value < -18) return "VERY FLAT";
    if (value > 18) return "VERY SHARP";
    return value < 0 ? "FLAT" : "SHARP";
  }

  function frequencyToNote(frequency) {
    if (!Number.isFinite(frequency) || frequency <= 0) return null;
    const midi = Math.round(69 + 12 * Math.log2(frequency / 440));
    const referenceFrequency = 440 * (2 ** ((midi - 69) / 12));
    const cents = 1200 * Math.log2(frequency / referenceFrequency);
    const noteIndex = ((midi % 12) + 12) % 12;
    return {
      name: `${NOTE_NAMES[noteIndex]}${Math.floor(midi / 12) - 1}`,
      cents,
      midi,
    };
  }

  function parabolicTau(values, tau) {
    if (tau <= 1 || tau >= values.length - 1) return tau;
    const left = values[tau - 1];
    const center = values[tau];
    const right = values[tau + 1];
    const denominator = left - (2 * center) + right;
    if (Math.abs(denominator) < 1e-12) return tau;
    return tau + (0.5 * (left - right) / denominator);
  }

  function detectPitch(samples, sampleRate) {
    if (!samples || samples.length < 1024 || !Number.isFinite(sampleRate) || sampleRate <= 0) return null;
    let energy = 0;
    for (let index = 0; index < samples.length; index += 1) {
      energy += samples[index] * samples[index];
    }
    const rms = Math.sqrt(energy / samples.length);
    if (rms < 0.012) return null;

    const minimumTau = Math.max(2, Math.floor(sampleRate / 1760));
    const maximumTau = Math.min(Math.floor(sampleRate / 55), Math.floor(samples.length / 2));
    if (maximumTau <= minimumTau) return null;
    const comparisonLength = samples.length - maximumTau;
    const difference = new Float32Array(maximumTau + 1);
    const normalized = new Float32Array(maximumTau + 1);

    for (let tau = 1; tau <= maximumTau; tau += 1) {
      let sum = 0;
      for (let index = 0; index < comparisonLength; index += 1) {
        const delta = samples[index] - samples[index + tau];
        sum += delta * delta;
      }
      difference[tau] = sum;
    }

    normalized[0] = 1;
    let runningSum = 0;
    for (let tau = 1; tau <= maximumTau; tau += 1) {
      runningSum += difference[tau];
      normalized[tau] = runningSum > 0 ? (difference[tau] * tau) / runningSum : 1;
    }

    const threshold = 0.13;
    for (let tau = minimumTau; tau < maximumTau; tau += 1) {
      if (normalized[tau] >= threshold) continue;
      while (tau + 1 < maximumTau && normalized[tau + 1] < normalized[tau]) tau += 1;
      const refinedTau = parabolicTau(normalized, tau);
      const clarity = 1 - normalized[tau];
      if (clarity < 0.82 || refinedTau <= 0) return null;
      return sampleRate / refinedTau;
    }
    return null;
  }

  function median(values) {
    if (!values.length) return null;
    const sorted = [...values].sort((left, right) => left - right);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  root.WWTuner = Object.freeze({ classifyCents, detectPitch, frequencyToNote, median });
})(typeof window !== "undefined" ? window : globalThis);
