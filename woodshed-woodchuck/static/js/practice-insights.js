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
        const row = document.createElement("p");
        row.textContent = `${week.week_start} – ${week.week_end}: ${window.WWPracticeDuration.seconds(week.seconds)} · ${week.days} practice days · ${window.WWPracticeDuration.seconds(week.verified_seconds)} verified · ${window.WWPracticeDuration.seconds(week.pristine_seconds)} Pristine`;
        content.append(row);
      }
      const summary = document.createElement("p");
      summary.textContent = `Four-week total: ${window.WWPracticeDuration.seconds(data.total_seconds)} · Weekly average: ${window.WWPracticeDuration.seconds(data.average_weekly_seconds)}`;
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
