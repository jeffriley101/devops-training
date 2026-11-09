(function () {
  "use strict";
  const form = document.getElementById("guest-classroom-entry");
  if (!form) return;
  const byId = id => document.getElementById(id);
  const status = byId("guest-classroom-status");
  const admit = byId("guest-classroom-admit");
  const practice = byId("guest-pristine");
  const start = byId("guest-pristine-start");
  const stop = byId("guest-pristine-stop");
  const leave = byId("guest-classroom-leave");
  const timeOutput = byId("guest-pristine-time");
  const microphone = byId("guest-pristine-status");
  let binding = null, deadline = 0, heartbeat = null, expiry = null;
  let ended = false, checking = false, generation = 0;
  let stream = null, audio = null, analyser = null, frame = null;
  let timer = window.PristinePracticeTimer.createTimer();
  let detector = window.PristinePracticeDetector.createDetector();

  function current() {
    return !ended && !document.hidden && window.WWSessionBoundary?.isCurrent();
  }
  function active() { return current() && binding && performance.now() < deadline; }
  function releaseMicrophone() {
    generation++;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    stream?.getTracks().forEach(track => track.stop());
    stream = null; analyser = null;
    const old = audio; audio = null;
    if (old) void old.close().catch(() => {});
    stop.disabled = true; start.disabled = !active();
  }
  function clearActivity() {
    releaseMicrophone();
    timer = window.PristinePracticeTimer.createTimer();
    detector = window.PristinePracticeDetector.createDetector();
    timeOutput.textContent = "00:00";
    microphone.textContent = "Microphone not started";
  }
  function end(message, permanent = false) {
    binding = null; deadline = 0; ended ||= permanent;
    clearTimeout(heartbeat); clearTimeout(expiry);
    clearActivity(); form.reset(); practice.hidden = true;
    admit.disabled = ended; leave.disabled = true;
    status.textContent = message;
    window.WWGuest?.reset();
  }
  async function request(action, data) {
    const response = await window.WWSessionBoundary.guestClassroomFetch(action, data);
    if (!response.ok) throw new Error("Guest Classroom could not be verified.");
    return response.json();
  }
  function accept(payload, began) {
    if (!current() || payload.active !== true || typeof payload.binding !== "string" ||
        payload.expires_in !== 900 || performance.now() - began >= 900000) {
      throw new Error("Guest Classroom could not be verified.");
    }
    binding = payload.binding;
    // Use elapsed monotonic time from BEFORE the request, never a wall-clock
    // change or a late response to extend an already expired browser context.
    deadline = began + 900000;
    clearTimeout(expiry); clearTimeout(heartbeat);
    expiry = setTimeout(() => end("Guest Classroom expired. Reload and enter a current code.", true),
      Math.max(0, deadline - performance.now()));
    heartbeat = setTimeout(() => { void revalidate(); }, 15000);
    practice.hidden = false; leave.disabled = false;
    status.textContent = "Temporary Class access active. Authority is checked during use; access expires after 15 minutes without successful renewal.";
  }
  async function revalidate() {
    if (!active() || checking) return false;
    checking = true;
    const expected = binding, priorDeadline = deadline, began = performance.now();
    try {
      const payload = await request("check", {binding: expected});
      if (binding !== expected || !current() || performance.now() >= priorDeadline) throw new Error();
      accept(payload, began);
      return true;
    } catch (_) {
      end("Class access could not be verified. Reload to enter a current code, or use general Guest Mode.", true);
      return false;
    } finally { checking = false; }
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!current() || admit.disabled || !form.reportValidity()) return;
    admit.disabled = true;
    const data = {program: Number(byId("guest-program").value), code: byId("guest-class-code").value};
    byId("guest-class-code").value = "";
    const began = performance.now();
    try { accept(await request("admit", data), began); start.disabled = false; }
    catch (_) { end("Guest Classroom is unavailable. Use a current Program and Class code."); }
    finally { admit.disabled = ended || Boolean(binding); }
  });
  async function exit() {
    const previous = binding;
    end("Guest Classroom ended. Your local activity was cleared.", true);
    if (previous && currentForExit()) {
      try { await request("leave", {binding: previous}); }
      catch (_) { status.textContent = "Local activity cleared. Exit could not be confirmed; the temporary context will expire. General Guest Mode remains available."; }
    }
  }
  function currentForExit() { return !document.hidden && window.WWSessionBoundary?.isCurrent(); }
  leave.addEventListener("click", () => { void exit(); });
  byId("guest-discard").addEventListener("click", () => { if (binding) void exit(); }, true);
  byId("guest-classroom-back").addEventListener("click", async event => {
    event.preventDefault(); await exit(); window.location.assign("/guest");
  });
  start.addEventListener("click", async () => {
    if (!active() || start.disabled) return;
    start.disabled = true;
    try {
      if (!await revalidate()) return;
      const expected = binding, version = generation;
      const acquired = await navigator.mediaDevices.getUserMedia({
        audio: {autoGainControl: false, echoCancellation: false, noiseSuppression: false}, video: false,
      });
      if (!active() || binding !== expected || version !== generation) {
        acquired.getTracks().forEach(track => track.stop()); return;
      }
      stream = acquired;
      audio = new (window.AudioContext || window.webkitAudioContext)();
      await audio.resume();
      if (!active() || version !== generation) { releaseMicrophone(); return; }
      analyser = audio.createAnalyser(); analyser.fftSize = 1024; analyser.smoothingTimeConstant = 0;
      audio.createMediaStreamSource(stream).connect(analyser);
      const samples = new Float32Array(analyser.fftSize), calibrateUntil = performance.now() + 1500;
      timer = window.PristinePracticeTimer.createTimer();
      detector = window.PristinePracticeDetector.createDetector();
      stop.disabled = false;
      function monitor(timestamp) {
        if (!active() || version !== generation || !analyser) {
          releaseMicrophone(); return;
        }
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        const before = timer.snapshot();
        const activity = detector.sample(rms, timestamp, {calibrating: timestamp < calibrateUntil,
          canContinue: before.hasPlayed && !before.paused});
        const state = timer.sample(timestamp, activity.detected);
        timeOutput.textContent = String(Math.floor(state.playingSeconds / 60)).padStart(2, "0") + ":" +
          String(state.playingSeconds % 60).padStart(2, "0");
        microphone.textContent = timestamp < calibrateUntil ? "Calibrating…" : state.status;
        frame = requestAnimationFrame(monitor);
      }
      frame = requestAnimationFrame(monitor);
    } catch (_) {
      releaseMicrophone(); microphone.textContent = "Microphone unavailable. Check browser permission and try again.";
    } finally { start.disabled = !active() || Boolean(stream); }
  });
  stop.addEventListener("click", () => {
    if (stream) timer.finish(performance.now());
    releaseMicrophone(); microphone.textContent = "Stopped. This time is local and has not been saved.";
  });
  window.addEventListener("ww:guest-reset", () => {
    clearActivity();
    if (binding) end("Guest activity cleared. Reload to enter Guest Classroom.", true);
  });
  window.addEventListener("ww:session-changed", () => end("Sign-in changed. Guest Classroom ended.", true));
  window.addEventListener("pagehide", () => end("Guest Classroom ended.", true));
  window.addEventListener("pageshow", event => { if (event.persisted) end("Reload to enter Guest Classroom.", true); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) end("Guest Classroom ended when this page was hidden. Reload to enter again.", true);
  });
  admit.disabled = false;
  status.textContent = "Enter the Music Program number and current Class code supplied by your director.";
})();
