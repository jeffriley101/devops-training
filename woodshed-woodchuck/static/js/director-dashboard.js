(function () {
  "use strict";

  const feedback = document.getElementById("director-dashboard-feedback");
  const content = document.getElementById("director-dashboard-content");
  const selector = document.getElementById("director-team-selector");
  const createForm = document.getElementById("director-team-create-form");
  const emblemSelect = document.getElementById("director-team-emblem");
  let payload = null;
  let currentTeam = null;
  const CENTRAL_TIME_ZONE = "America/Chicago";

  async function request(url, options = {}) {
    const response = await fetch(url, {credentials: "same-origin", ...options});
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "The Director Dashboard is unavailable.");
    return body;
  }

  function actionButton(label, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary";
    button.textContent = label;
    button.addEventListener("click", handler);
    return button;
  }

  function renderBars(root, rows, labelKey) {
    root.replaceChildren();
    const highest = Math.max(1, ...rows.map((row) => Number(row.minutes) || 0));
    rows.forEach((row) => {
      const line = document.createElement("div");
      line.className = "director-bar-row";
      const label = document.createElement("span");
      label.textContent = row[labelKey];
      const track = document.createElement("span");
      track.className = "director-bar-track";
      const bar = document.createElement("span");
      bar.className = "director-bar-fill";
      bar.style.width = `${Math.max(0, Number(row.minutes) || 0) * 100 / highest}%`;
      const value = document.createElement("strong");
      value.textContent = String(row.minutes);
      line.setAttribute("aria-label", `${row[labelKey]}: ${row.minutes} minutes`);
      track.appendChild(bar);
      line.append(label, track, value);
      root.appendChild(line);
    });
    if (!rows.length) root.textContent = "No practice yet.";
  }

  function renderMetrics(data) {
    const metrics = data.metrics;
    document.getElementById("director-total-minutes").textContent = metrics.total_practice_minutes;
    document.getElementById("director-average-minutes").textContent = metrics.average_minutes;
    document.getElementById("director-participation").textContent = `${metrics.participation.active} / ${metrics.participation.eligible}`;
    document.getElementById("director-participation-percent").textContent = `${metrics.participation.percent}%`;
    document.getElementById("director-pcharts").textContent = metrics.p_charts.submitted;
    document.getElementById("director-pchart-breakdown").textContent = `${metrics.p_charts.verified} verified · ${metrics.p_charts.pending} pending`;
    document.getElementById("director-consistency").textContent = `${metrics.consistency.days} / ${metrics.consistency.elapsed_days} days`;
    document.getElementById("director-consistency-percent").textContent = `${metrics.consistency.percent}%`;
    document.getElementById("director-tpr").textContent = Number(metrics.team_practice_rating).toFixed(1);
    document.getElementById("director-verifier-link").hidden = data.verifier_queue_available !== true;
    renderBars(document.getElementById("director-daily-chart"), data.charts.daily_practice || [], "label");
    renderBars(document.getElementById("director-instrument-chart"), data.charts.by_instrument || [], "instrument");
  }

  function renderManagement(team) {
    document.getElementById("director-team-code").textContent = team.join_code;
    document.getElementById("director-team-join-playing").hidden = team.director_is_playing_member;
    const pending = document.getElementById("director-team-pending");
    pending.replaceChildren();
    (team.pending_requests || []).forEach((row) => {
      const item = document.createElement("p");
      item.append(document.createTextNode(`${row.display_name} `));
      item.append(
        actionButton("Approve", () => resolveRequest(row.id, "approve")),
        actionButton("Reject", () => resolveRequest(row.id, "reject"))
      );
      pending.appendChild(item);
    });
    if (!pending.childElementCount) pending.textContent = "No pending requests.";

    const members = document.getElementById("director-team-members");
    members.replaceChildren();
    (team.members || []).forEach((row) => {
      const item = document.createElement("p");
      item.append(document.createTextNode(`${row.display_name} `));
      item.append(actionButton("Remove", async () => {
        await request(`/teams/director/${team.id}/members/${row.profile_id}`, {method: "DELETE"});
        await load(team.id);
      }));
      members.appendChild(item);
    });
    if (!members.childElementCount) members.textContent = "No playing members yet.";
  }

  function centralIso(value) {
    const assumedUtc = new Date(`${value}:00Z`);
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Chicago", year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(assumedUtc).filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]));
    const represented = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
    return new Date(assumedUtc.getTime() - (represented - assumedUtc.getTime())).toISOString();
  }

  function formatCentralDateTime(value) {
    return `${new Intl.DateTimeFormat(undefined, {
      timeZone: CENTRAL_TIME_ZONE,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value))} CT`;
  }

  function renderContestSetup(data) {
    const metricSelect = document.getElementById("director-contest-metric");
    if (!metricSelect.options.length) {
      Object.entries(data.contest_metrics || {}).forEach(([key, label]) => {
        metricSelect.append(new Option(label, key));
      });
    }
    const fieldset = document.getElementById("director-contest-teams");
    fieldset.querySelectorAll("label").forEach((label) => label.remove());
    (data.teams || []).forEach((team) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = "director-contest-team";
      input.value = team.id;
      input.checked = team.id === currentTeam.id;
      label.append(input, document.createTextNode(` ${team.emblem.value} ${team.name}`));
      fieldset.appendChild(label);
    });
  }

  function renderContestList(contests) {
    const list = document.getElementById("director-contest-list");
    list.replaceChildren();
    contests.forEach((contest) => {
      const card = document.createElement("article");
      card.className = "director-contest-card";
      const title = document.createElement("h3");
      title.textContent = contest.title;
      const details = document.createElement("p");
      details.textContent = `${contest.metric_label} · ${formatCentralDateTime(contest.starts_at)} – ${formatCentralDateTime(contest.ends_at)} · ${contest.status}`;
      card.append(title, details);
      if (contest.description) {
        const description = document.createElement("p");
        description.textContent = contest.description;
        card.appendChild(description);
      }
      if (contest.results.length) {
        const results = document.createElement("ol");
        contest.results.forEach((row) => {
          const item = document.createElement("li");
          item.value = row.rank;
          item.textContent = `${row.emblem.value} ${row.team_name} — ${row.score}`;
          results.appendChild(item);
        });
        card.appendChild(results);
      } else if (contest.status !== "finalized") {
        card.appendChild(actionButton("Finalize Results", async () => {
          try {
            await request(`/director/contests/${contest.id}/finalize`, {method: "POST"});
            await loadContests();
          } catch (error) { feedback.textContent = error.message; }
        }));
      }
      list.appendChild(card);
    });
    if (!contests.length) list.textContent = "No team contests yet.";
  }

  async function loadContests() {
    const result = await request("/director/contests");
    renderContestList(result.contests || []);
  }

  function fillTeamSelector(data) {
    const previous = selector.value;
    selector.replaceChildren();
    (data.teams || []).forEach((team) => {
      selector.append(new Option(`${team.emblem.value} ${team.name}`, team.id));
    });
    selector.value = currentTeam ? String(currentTeam.id) : previous;
  }

  async function load(teamId = null) {
    try {
      payload = await request(`/director/dashboard${teamId ? `?team_id=${teamId}` : ""}`);
      currentTeam = payload.team || null;
      fillTeamSelector(payload);
      content.hidden = !currentTeam;
      feedback.textContent = currentTeam ? "" : "Create a private team to begin.";
      if (!currentTeam) return;
      renderMetrics(payload);
      renderManagement(currentTeam);
      renderContestSetup(payload);
      await loadContests();
    } catch (error) { feedback.textContent = error.message; }
  }

  async function resolveRequest(requestId, action) {
    try {
      await request(`/teams/director/${currentTeam.id}/requests/${requestId}`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action}),
      });
      await load(currentTeam.id);
    } catch (error) { feedback.textContent = error.message; }
  }

  selector.addEventListener("change", () => load(Number(selector.value)));
  document.getElementById("director-new-team-toggle").addEventListener("click", () => {
    createForm.hidden = !createForm.hidden;
  });
  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await request("/teams/director", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          name: document.getElementById("director-team-name").value,
          emblem_key: emblemSelect.value,
        }),
      });
      createForm.reset();
      createForm.hidden = true;
      await load(result.team.id);
    } catch (error) { feedback.textContent = error.message; }
  });
  document.getElementById("director-team-regenerate").addEventListener("click", async () => {
    try {
      await request(`/teams/director/${currentTeam.id}/join-code`, {method: "POST"});
      await load(currentTeam.id);
    } catch (error) { feedback.textContent = error.message; }
  });
  document.getElementById("director-team-join-playing").addEventListener("click", async () => {
    try {
      await request(`/teams/director/${currentTeam.id}/playing-membership`, {method: "POST"});
      await load(currentTeam.id);
    } catch (error) { feedback.textContent = error.message; }
  });
  document.getElementById("director-contest-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const selectedTeamIds = Array.from(document.querySelectorAll("input[name=director-contest-team]:checked"))
      .map((input) => Number(input.value));
    try {
      await request("/director/contests", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          title: document.getElementById("director-contest-title").value,
          description: document.getElementById("director-contest-description").value,
          metric: document.getElementById("director-contest-metric").value,
          starts_at: centralIso(document.getElementById("director-contest-start").value),
          ends_at: centralIso(document.getElementById("director-contest-end").value),
          finalizes_at: centralIso(document.getElementById("director-contest-finalizes").value),
          team_ids: selectedTeamIds,
        }),
      });
      event.target.reset();
      await loadContests();
    } catch (error) { feedback.textContent = error.message; }
  });

  request("/teams/director").then((initial) => {
    (initial.approved_emblems || []).forEach((emblem) => {
      emblemSelect.append(new Option(emblem.value, emblem.key));
    });
    return load();
  }).catch((error) => { feedback.textContent = error.message; });
})();
