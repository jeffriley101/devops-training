(function () {
  "use strict";
  const panel = document.getElementById("practice-insights");
  if (!panel) return;
  const locked = panel.querySelector("[data-insights-locked]");
  const active = panel.querySelector("[data-insights-active]");
  const content = panel.querySelector("[data-insights-content]");
  const status = panel.querySelector("[data-insights-status]");
  let generation = 0;

  function clear() {
    generation += 1;
    content.replaceChildren();
  }

  function metric(list, label, value) {
    const group = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    group.append(term, detail);
    list.append(group);
  }

  async function refresh() {
    clear();
    const requestGeneration = generation;
    status.textContent = "Loading Practice Insights…";
    try {
      const response = await fetch("/practice-charts/insights", {
        credentials: "same-origin", cache: "no-store"
      });
      if (requestGeneration !== generation) return;
      if (response.status === 401 || response.status === 403) {
        clear();
        active.hidden = true;
        locked.hidden = false;
        return;
      }
      if (!response.ok) throw new Error("unavailable");
      const data = await response.json();
      if (requestGeneration !== generation) return;
      locked.hidden = true;
      active.hidden = false;
      for (const week of data.weeks) {
        const row = document.createElement("section");
        row.className = "practice-insights-week";
        const heading = document.createElement("h3");
        heading.textContent = `${week.week_start} – ${week.week_end}`;
        const values = document.createElement("dl");
        metric(values, "Reported practice", window.WWPracticeDuration.seconds(week.seconds));
        metric(values, "Practice days", String(week.days));
        metric(values, "Verified", window.WWPracticeDuration.seconds(week.verified_seconds));
        metric(values, "Pristine", window.WWPracticeDuration.seconds(week.pristine_seconds));
        row.append(heading, values);
        content.append(row);
      }
      const summary = document.createElement("dl");
      summary.className = "practice-insights-summary";
      metric(summary, "Four-week total", window.WWPracticeDuration.seconds(data.total_seconds));
      metric(summary, "Weekly average", window.WWPracticeDuration.seconds(data.average_weekly_seconds));
      content.append(summary);
      status.textContent = "";
    } catch (_) {
      if (requestGeneration !== generation) return;
      content.replaceChildren();
      status.textContent = "Practice Insights is temporarily unavailable. Reload to try again.";
    }
  }

  if (panel.dataset.enabled === "true") refresh();
  window.addEventListener("pagehide", clear);
  window.addEventListener("pageshow", (event) => { if (event.persisted) refresh(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clear();
    else if (panel.dataset.enabled === "true") refresh();
  });
}());
