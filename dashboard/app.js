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

async function init() {
  try {
    const site = await loadJson("data/site-sync-status.json");
    const siteStatus = document.getElementById("site-sync-status");
    siteStatus.textContent = `Last dashboard sync: ${site.last_synced_utc}`;
  } catch (error) {
    const siteStatus = document.getElementById("site-sync-status");
    siteStatus.textContent = "Latest dashboard sync status unavailable.";
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
    document.getElementById("env-inspector-metadata").innerHTML =
      "<p>Latest metadata unavailable.</p>";
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
    document.getElementById("internet-health-metadata").innerHTML =
      "<p>Latest metadata unavailable.</p>";
  }
}

init();
