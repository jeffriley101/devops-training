async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status}`);
  }
  return response.json();
}

function renderMetadata(containerId, data, fields) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const rows = fields
    .filter((field) => data[field.key] !== undefined && data[field.key] !== null && data[field.key] !== "")
    .map((field) => `<dt>${field.label}</dt><dd>${data[field.key]}</dd>`)
    .join("");

  container.innerHTML = rows ? `<dl>${rows}</dl>` : "<p>No metadata available.</p>";
}

let internetHistoryData = [];
let internetHealthChart = null;

const INTERNET_HEALTH_SERIES_ORDER = [
  "google",
  "timeanddate",
  "groundnews",
  "weathergov",
  "aws",
  "homestarrunner"
];

const INTERNET_HEALTH_SERIES_COLORS = {
  google: "#2563eb",
  timeanddate: "#16a34a",
  groundnews: "#dc2626",
  weathergov: "#7c3aed",
  aws: "#f59e0b",
  homestarrunner: "#0891b2"
};

function normalizeInternetHistory(rawData) {
  if (!Array.isArray(rawData)) {
    return [];
  }

  return rawData
    .map((row) => {
      if (!row || typeof row !== "object") {
        return null;
      }

      const timestamp =
        row.timestamp ||
        row.timestamp_utc ||
        row.timestamp_utc_human ||
        row.checked_at_utc ||
        row.recorded_at_utc;

      if (!timestamp) {
        return null;
      }

      const normalized = { timestamp };

      for (const key of INTERNET_HEALTH_SERIES_ORDER) {
        const value = row[key];
        if (typeof value === "number" && Number.isFinite(value)) {
          normalized[key] = value;
        }
      }

      return normalized;
    })
    .filter((row) => row && row.timestamp);
}

function getInternetHealthSeriesNames(rows) {
  const present = new Set();

  for (const row of rows) {
    for (const key of INTERNET_HEALTH_SERIES_ORDER) {
      if (typeof row[key] === "number" && Number.isFinite(row[key])) {
        present.add(key);
      }
    }
  }

  return INTERNET_HEALTH_SERIES_ORDER.filter((key) => present.has(key));
}

function destroyInternetHealthChart() {
  if (internetHealthChart) {
    internetHealthChart.destroy();
    internetHealthChart = null;
  }
}

function renderInternetHealthChart(rows) {
  const canvas = document.getElementById("internet-health-history-chart");
  if (!canvas || typeof Chart === "undefined") {
    updateInternetHealthHistoryStatus("Chart unavailable.");
    return;
  }

  destroyInternetHealthChart();

  const seriesNames = getInternetHealthSeriesNames(rows);
  if (!rows.length || !seriesNames.length) {
    updateInternetHealthHistoryStatus("No chartable history data available.");
    return;
  }

  const ctx = canvas.getContext("2d");

  const datasets = seriesNames.map((name) => ({
    label: name,
    data: rows.map((row) =>
      typeof row[name] === "number" && Number.isFinite(row[name]) ? row[name] : null
    ),
    borderColor: INTERNET_HEALTH_SERIES_COLORS[name] || "#374151",
    backgroundColor: INTERNET_HEALTH_SERIES_COLORS[name] || "#374151",
    borderWidth: 1,
    pointRadius: 1,
    pointHoverRadius: 3,
    tension: 0,
    spanGaps: true
  }));

  internetHealthChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: rows.map((row) => row.timestamp),
      datasets
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "nearest",
        intersect: false
      },
      plugins: {
        legend: {
          position: "bottom"
        }
      },
      scales: {
        x: {
          ticks: {
            maxRotation: 45,
            minRotation: 45
          }
        },
        y: {
          beginAtZero: true,
          title: {
            display: true,
            text: "Latency (ms)"
          }
        }
      }
    }
  });
}

function updateInternetHealthHistoryStatus(message) {
  const statusEl = document.getElementById("internet-health-history-status");
  if (statusEl) {
    statusEl.textContent = message;
  }
}

function applyInternetHealthLookback(count) {
  if (!internetHistoryData.length) {
    updateInternetHealthHistoryStatus("No history data available.");
    return;
  }

  let rows = internetHistoryData;

  if (Number.isFinite(count) && count > 0) {
    rows = internetHistoryData.slice(-count);
  }

  renderInternetHealthChart(rows);

  const latest = rows.length ? rows[rows.length - 1].timestamp : "none";
  const seriesNames = getInternetHealthSeriesNames(rows);

  updateInternetHealthHistoryStatus(
    `Showing ${rows.length} point(s) across ${seriesNames.length} target(s). Latest timestamp: ${latest}`
  );
}

function setupInternetHealthControls() {
  const applyBtn = document.getElementById("internet-health-apply-btn");
  const allBtn = document.getElementById("internet-health-all-btn");
  const input = document.getElementById("internet-health-lookback");

  if (applyBtn && input) {
    applyBtn.addEventListener("click", () => {
      const count = parseInt(input.value, 10);
      applyInternetHealthLookback(count);
    });
  }

  if (allBtn) {
    allBtn.addEventListener("click", () => {
      applyInternetHealthLookback(internetHistoryData.length);
    });
  }

  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        const count = parseInt(input.value, 10);
        applyInternetHealthLookback(count);
      }
    });
  }
}

async function init() {
  try {
    const site = await loadJson("data/site-sync-status.json");
    const siteStatus = document.getElementById("site-sync-status");
    if (siteStatus) {
      siteStatus.textContent = `Last dashboard sync: ${site.last_synced_utc}`;
    }
  } catch (error) {
    const siteStatus = document.getElementById("site-sync-status");
    if (siteStatus) {
      siteStatus.textContent = "Latest dashboard sync status unavailable.";
    }
  }

  try {
    const envInspector = await loadJson("data/env-inspector-latest.json");
    renderMetadata("env-inspector-metadata", envInspector, [
      { key: "timestamp_utc_human", label: "Last Updated" },
      { key: "status", label: "Status" },
      { key: "run_source", label: "Run Source" },
      { key: "git_sha", label: "Git SHA" },
      { key: "task_def_arn", label: "Task Definition" }
    ]);
  } catch (error) {
    const el = document.getElementById("env-inspector-metadata");
    if (el) {
      el.innerHTML = "<p>Latest metadata unavailable.</p>";
    }
  }

  try {
    const internetHealth = await loadJson("data/internet-health-latest.json");
    renderMetadata("internet-health-metadata", internetHealth, [
      { key: "timestamp_utc_human", label: "Last Updated" },
      { key: "generated_chart", label: "Chart Generated" },
      { key: "total_targets", label: "Targets Checked" },
      { key: "healthy_count", label: "Healthy" },
      { key: "degraded_count", label: "Degraded" },
      { key: "unhealthy_count", label: "Unhealthy" }
    ]);
  } catch (error) {
    const el = document.getElementById("internet-health-metadata");
    if (el) {
      el.innerHTML = "<p>Latest metadata unavailable.</p>";
    }
  }

  setupInternetHealthControls();

  try {
    const rawHistory = await loadJson("data/internet-health-history.json");
    internetHistoryData = normalizeInternetHistory(rawHistory);

    if (!internetHistoryData.length) {
      updateInternetHealthHistoryStatus("No history data available.");
      return;
    }

    const input = document.getElementById("internet-health-lookback");
    const defaultCount = input ? parseInt(input.value, 10) : 50;
    applyInternetHealthLookback(defaultCount);
  } catch (error) {
    console.error(error);
    updateInternetHealthHistoryStatus("Internet health history unavailable.");
  }
}

init();
