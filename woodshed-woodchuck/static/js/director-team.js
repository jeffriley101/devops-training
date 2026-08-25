(function () {
  "use strict";

  const feedback = document.getElementById("director-team-feedback");
  const createForm = document.getElementById("director-team-create-form");
  const content = document.getElementById("director-team-content");
  const emblemSelect = document.getElementById("director-team-emblem");
  let currentTeam = null;

  async function request(url, options = {}) {
    const response = await fetch(url, {credentials: "same-origin", ...options});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Team management is unavailable.");
    return payload;
  }

  function actionButton(label, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary";
    button.textContent = label;
    button.addEventListener("click", handler);
    return button;
  }

  function render(payload) {
    currentTeam = payload.team || null;
    createForm.hidden = Boolean(currentTeam);
    content.hidden = !currentTeam;
    if (emblemSelect.options.length === 1) {
      (payload.approved_emblems || []).forEach((emblem) => {
        emblemSelect.append(new Option(emblem.value, emblem.key));
      });
    }
    if (!currentTeam) {
      feedback.textContent = "Create a private team for your students.";
      return;
    }
    feedback.textContent = "";
    document.getElementById("director-team-name-display").textContent = currentTeam.name;
    document.getElementById("director-team-code").textContent = currentTeam.join_code;
    document.getElementById("director-team-join-playing").hidden = currentTeam.director_is_playing_member;

    const pending = document.getElementById("director-team-pending");
    pending.replaceChildren();
    (currentTeam.pending_requests || []).forEach((row) => {
      const item = document.createElement("p");
      item.append(document.createTextNode(`${row.display_name} `));
      item.append(
        actionButton("Approve", () => resolveRequest(row.id, "approve")),
        actionButton("Reject", () => resolveRequest(row.id, "reject"))
      );
      pending.append(item);
    });
    if (!pending.childElementCount) pending.textContent = "No pending requests.";

    const members = document.getElementById("director-team-members");
    members.replaceChildren();
    (currentTeam.members || []).forEach((row) => {
      const item = document.createElement("p");
      item.append(document.createTextNode(`${row.display_name} `));
      item.append(actionButton("Remove", async () => {
        await request(`/teams/director/${currentTeam.id}/members/${row.profile_id}`, {method: "DELETE"});
        await load();
      }));
      members.append(item);
    });
    if (!members.childElementCount) members.textContent = "No playing members yet.";
  }

  async function load() {
    try { render(await request("/teams/director")); }
    catch (error) { feedback.textContent = error.message; }
  }

  async function resolveRequest(requestId, action) {
    try {
      await request(`/teams/director/${currentTeam.id}/requests/${requestId}`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action}),
      });
      await load();
    } catch (error) { feedback.textContent = error.message; }
  }

  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = await request("/teams/director", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          name: document.getElementById("director-team-name").value,
          emblem_key: emblemSelect.value,
        }),
      });
      render(payload);
    } catch (error) { feedback.textContent = error.message; }
  });

  document.getElementById("director-team-regenerate").addEventListener("click", async () => {
    try {
      await request(`/teams/director/${currentTeam.id}/join-code`, {method: "POST"});
      await load();
    } catch (error) { feedback.textContent = error.message; }
  });
  document.getElementById("director-team-join-playing").addEventListener("click", async () => {
    try {
      await request(`/teams/director/${currentTeam.id}/playing-membership`, {method: "POST"});
      await load();
    } catch (error) { feedback.textContent = error.message; }
  });

  load();
})();
