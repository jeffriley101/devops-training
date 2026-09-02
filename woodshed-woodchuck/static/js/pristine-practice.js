(function () {
  "use strict";

  const root = document.querySelector("[data-pristine-practice]");
  if (!root || !window.PristinePracticeTimer || !window.PristinePracticeDetector) return;

  const timerOutput = root.querySelector("[data-pristine-time]");
  const statusOutput = root.querySelector("[data-pristine-status]");
  const microphoneOutput = root.querySelector("[data-pristine-microphone]");
  const feedback = root.querySelector("[data-pristine-feedback]");
  const startButton = root.querySelector("[data-pristine-start]");
  const pauseButton = root.querySelector("[data-pristine-pause]");
  const doneButton = root.querySelector("[data-pristine-done]");
  const retryButton = root.querySelector("[data-pristine-retry]");
  let timer = window.PristinePracticeTimer.createTimer();
  let stream = null;
  let audioContext = null;
  let analyser = null;
  let samples = null;
  let frameId = null;
  let calibrationUntil = 0;
  let detector = window.PristinePracticeDetector.createDetector();
  let sessionStarted = false;
  let submissionKey = null;
  let submitting = false;

  function formatTime(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    return [hours, minutes, remainder]
      .map(function (value) { return String(value).padStart(2, "0"); })
      .join(":");
  }

  function statusText(state) {
    if (state.status === "playing") return "Playing";
    if (state.status === "safety-paused") return "Paused at 60 minutes — press Resume to continue";
    if (state.status === "paused") return "Paused";
    if (state.status === "done") return "Done";
    if (state.silenceMilliseconds > 0) {
      const remaining = Math.max(0, 8 - Math.floor(state.silenceMilliseconds / 1000));
      return `Listening · pauses after ${remaining}s of silence`;
    }
    return "Listening";
  }

  function render(state) {
    timerOutput.textContent = formatTime(state.playingSeconds);
    statusOutput.textContent = statusText(state);
    root.dataset.pristineState = state.status;
    pauseButton.disabled = !sessionStarted || state.done || submitting;
    doneButton.disabled = !sessionStarted || state.done || submitting;
    pauseButton.textContent = state.paused ? "Resume" : "Pause";
    startButton.hidden = sessionStarted && !state.done;
  }

  function rmsValue() {
    analyser.getFloatTimeDomainData(samples);
    let energy = 0;
    for (let index = 0; index < samples.length; index += 1) {
      energy += samples[index] * samples[index];
    }
    return Math.sqrt(energy / samples.length);
  }

  function monitor(timestamp) {
    if (!analyser || !sessionStarted || timer.snapshot().done) return;
    const calibrating = timestamp < calibrationUntil;
    const previousState = timer.snapshot();
    const activity = detector.sample(rmsValue(), timestamp, {
      calibrating,
      canContinue: previousState.hasPlayed && !previousState.paused,
    });
    const active = activity.detected;
    microphoneOutput.textContent = calibrating
      ? "Calibrating to the room…"
      : active ? "Sound detected" : "Microphone ready";
    render(timer.sample(timestamp, active));
    frameId = window.requestAnimationFrame(monitor);
  }

  async function stopMicrophone() {
    if (frameId !== null) window.cancelAnimationFrame(frameId);
    frameId = null;
    if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
    stream = null;
    analyser = null;
    samples = null;
    if (audioContext) await audioContext.close().catch(function () {});
    audioContext = null;
  }

  function resetSession() {
    timer = window.PristinePracticeTimer.createTimer();
    calibrationUntil = 0;
    detector = window.PristinePracticeDetector.createDetector();
    sessionStarted = false;
    submissionKey = null;
    delete retryButton.dataset.retrySave;
    retryButton.textContent = "Retry Microphone";
    feedback.textContent = "";
    microphoneOutput.textContent = "Microphone not started";
    startButton.textContent = "Enable Microphone & Start";
    startButton.hidden = false;
    retryButton.hidden = true;
    render(timer.snapshot());
  }

  async function startSession() {
    if (root.dataset.authenticated !== "true") {
      feedback.textContent = "Sign in before starting Pristine Practice.";
      return;
    }
    startButton.disabled = true;
    retryButton.hidden = true;
    feedback.textContent = "";
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("This browser cannot use a microphone for Pristine Practice.");
      }
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          autoGainControl: false,
          echoCancellation: false,
          noiseSuppression: false,
        },
        video: false,
      });
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error("Web Audio is unavailable in this browser.");
      audioContext = new AudioContextClass();
      await audioContext.resume();
      const source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0;
      source.connect(analyser);
      samples = new Float32Array(analyser.fftSize);
      timer = window.PristinePracticeTimer.createTimer();
      sessionStarted = true;
      submissionKey = window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : `pristine-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      calibrationUntil = performance.now() + 1500;
      microphoneOutput.textContent = "Calibrating to the room…";
      render(timer.snapshot());
      frameId = window.requestAnimationFrame(monitor);
    } catch (error) {
      await stopMicrophone();
      sessionStarted = false;
      microphoneOutput.textContent = "Microphone unavailable";
      feedback.textContent = error && error.message
        ? `${error.message} Check microphone permission, then retry.`
        : "Microphone permission was not granted. Check permission, then retry.";
      retryButton.hidden = false;
    } finally {
      startButton.disabled = false;
    }
  }

  async function finishSession() {
    if (!sessionStarted || submitting) return;
    const currentState = timer.snapshot();
    if (currentState.playingSeconds < 1) {
      feedback.textContent = "No playing was detected yet. Play something before pressing Done.";
      render(currentState);
      return;
    }
    const finalState = timer.finish(performance.now());
    render(finalState);
    submitting = true;
    await stopMicrophone();
    feedback.textContent = "Saving Pristine P-Chart…";
    try {
      const response = await fetch("/practice-charts/pristine", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          detected_playing_seconds: finalState.playingSeconds,
          submission_key: submissionKey,
          include_contests: true,
          include_team_contests: true,
        }),
      });
      const payload = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(payload.detail || "The Pristine P-Chart could not be saved.");
      feedback.textContent = payload.created
        ? `Pristine P-Chart saved: ${formatTime(payload.chart.detected_playing_seconds)} detected playing time.`
        : "This Pristine P-Chart was already saved.";
      startButton.textContent = "Start Another Session";
      startButton.hidden = false;
    } catch (error) {
      feedback.textContent = error.message || "The Pristine P-Chart could not be saved.";
      retryButton.textContent = "Retry Save";
      retryButton.hidden = false;
      retryButton.dataset.retrySave = "true";
    } finally {
      submitting = false;
      render(timer.snapshot());
    }
  }

  startButton.addEventListener("click", async function () {
    if (timer.snapshot().done) resetSession();
    await startSession();
  });
  pauseButton.addEventListener("click", function () {
    const now = performance.now();
    const state = timer.snapshot().paused ? timer.resume(now) : timer.pause(now);
    render(state);
  });
  doneButton.addEventListener("click", finishSession);
  retryButton.addEventListener("click", async function () {
    if (retryButton.dataset.retrySave === "true") {
      retryButton.hidden = true;
      await finishSession();
      return;
    }
    await startSession();
  });
  window.addEventListener("pagehide", function () { void stopMicrophone(); });
  resetSession();
}());
