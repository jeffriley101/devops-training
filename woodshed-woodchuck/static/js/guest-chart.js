(function () {
  "use strict";
  if (document.body.dataset.guest !== "local") return;
  const form = document.getElementById("guest-chart-form");
  const fields = document.getElementById("guest-chart-fields");
  const date = document.getElementById("guest-chart-date");
  const instrument = document.getElementById("guest-chart-instrument");
  const minutes = document.getElementById("guest-chart-minutes");
  const feedback = document.getElementById("guest-chart-feedback");
  const preview = document.getElementById("guest-chart-preview");
  const create = document.getElementById("guest-chart-create");
  const share = document.getElementById("guest-chart-share");
  const download = document.getElementById("guest-chart-download");
  const copy = document.getElementById("guest-chart-copy");
  if (!form || !fields || !date || !instrument || !minutes || !feedback || !preview || !create || !share || !download || !copy) return;
  const details = Array.from(form.querySelectorAll("[data-guest-detail]"));
  const instruments = new Set(Array.from(instrument.options).map(option => option.value).filter(Boolean));
  const categories = new Set(["Long Tones", "Arpeggios", "Etudes", "Patterns", "Dynamics", "Articulations", "Transcribing", "Ear Training", "Tuning", "Metronome Work", "Band Class", "Lessons", "Rehearsals", "Concerts", "Tests", "Performances", "Busking", "Warm-ups", "Scales", "Songs", "Improvisation", "Auditions", "Fingerings"]);
  let chart = null;
  let generation = 0;
  let busy = false;

  function current() {
    return window.WWGuest?.isCurrent() === true;
  }
  function actions() {
    for (const button of [share, download, copy]) button.disabled = chart === null || busy;
  }
  function reset() {
    generation += 1;
    chart = null;
    busy = false;
    form.reset();
    date.value = "";
    preview.textContent = "";
    preview.hidden = true;
    feedback.textContent = "";
    fields.disabled = true;
    actions();
  }
  function text() {
    return ["Woodshed Woodchuck Guest P-Chart", "", `Practice date: ${chart.date}`,
      `Instrument: ${chart.instrument}`, `Practice minutes: ${chart.minutes}`,
      `Practice details: ${chart.details.length ? chart.details.join(", ") : "None selected"}`, ""].join("\n");
  }
  function filename() {
    return `woodshed-guest-p-chart-${chart.date}.txt`;
  }
  function build(event) {
    event?.preventDefault();
    if (!current() || busy) return;
    const duration = Number(minutes.value);
    const parsed = new Date(`${date.value}T00:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date.value) || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date.value) {
      feedback.textContent = "Choose a valid practice date.";
      return;
    }
    if (!instruments.has(instrument.value)) {
      feedback.textContent = "Choose a supported instrument.";
      return;
    }
    if (!Number.isInteger(duration) || duration < 1 || duration > 1440) {
      feedback.textContent = "Enter whole minutes between 1 and 1440.";
      return;
    }
    const selected = details.filter(item => item.checked).map(item => item.value);
    if (selected.some(value => !categories.has(value))) {
      feedback.textContent = "Choose from the listed practice details.";
      return;
    }
    chart = {date: date.value, instrument: instrument.value, minutes: duration, details: selected};
    generation += 1;
    preview.textContent = text();
    preview.hidden = false;
    feedback.textContent = "Your chart is ready. Choose Share, Download or Copy to keep a copy.";
    actions();
  }
  function downloadChart() {
    if (!current() || !chart || busy) return;
    const blob = new Blob([text()], {type: "text/plain;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename();
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    feedback.textContent = "Chart downloaded.";
  }
  create.addEventListener("click", build);
  form.addEventListener("submit", build);
  download.addEventListener("click", downloadChart);
  share.addEventListener("click", async function () {
    if (!current() || !chart || busy) return;
    if (typeof navigator.share !== "function") {
      downloadChart();
      return;
    }
    let payload = {title: "Guest P-Chart", text: text()};
    if (typeof File === "function" && typeof navigator.canShare === "function") {
      const file = new File([text()], filename(), {type: "text/plain"});
      if (navigator.canShare({files: [file]})) payload = {title: "Guest P-Chart", files: [file]};
    }
    if (typeof navigator.canShare === "function" && !navigator.canShare(payload)) {
      downloadChart();
      return;
    }
    const expected = generation;
    busy = true;
    actions();
    try {
      await navigator.share(payload);
      if (expected === generation && current()) feedback.textContent = "Chart shared.";
    } catch (error) {
      if (expected === generation && current()) {
        feedback.textContent = error?.name === "AbortError" ? "Sharing cancelled." : "Sharing was unavailable. You can Download or Copy the chart.";
      }
    } finally {
      if (expected === generation) {
        busy = false;
        actions();
      }
    }
  });
  copy.addEventListener("click", async function () {
    if (!current() || !chart || busy) return;
    const expected = generation;
    busy = true;
    actions();
    try {
      if (typeof navigator.clipboard?.writeText !== "function") throw new Error();
      await navigator.clipboard.writeText(text());
      if (expected === generation && current()) feedback.textContent = "Chart copied.";
    } catch (_error) {
      if (expected === generation && current()) feedback.textContent = "Copy was unavailable. You can Download the chart.";
    } finally {
      if (expected === generation) {
        busy = false;
        actions();
      }
    }
  });
  window.addEventListener("ww:guest-reset", reset);
  window.addEventListener("ww:guest-setup", function (event) {
    if (!current()) return;
    fields.disabled = false;
    if (!date.value) {
      const now = new Date();
      date.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    }
    if (!instrument.value && instruments.has(event.detail.instrument)) instrument.value = event.detail.instrument;
  });
  reset();
  window.WWGuestChart = Object.freeze({ready: true});
})();
