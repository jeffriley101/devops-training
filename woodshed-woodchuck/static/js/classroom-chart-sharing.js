/* Private-practice adapter for the existing individual P-Chart sharing API. */
(() => {
  "use strict";
  const cards = [...document.querySelectorAll("[data-classroom-chart]")];
  if (!cards.length) return;
  const make = (tag, text) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    return element;
  };
  const request = (url, options = {}) => fetch(url, {
    credentials: "same-origin", cache: "no-store", ...options
  });
  request("/classroom/chart-sharing").then(async response => {
    if (!response.ok) return;
    const available = await response.json();
    if (!available.choices?.length && !available.has_shares) return;
    for (const card of cards) {
      const open = card.querySelector("[data-chart-sharing-open]");
      const editor = card.querySelector("[data-chart-sharing-editor]");
      open.hidden = false;
      open.addEventListener("click", async () => {
        editor.hidden = false;
        editor.replaceChildren(make("p", "Loading current sharing choices…"));
        try {
          const response = await request(`/classroom/chart-sharing/${card.dataset.classroomChart}`);
          if (!response.ok) throw new Error("unavailable");
          const data = await response.json();
          const form = make("form");
          const label = make("label");
          label.className = "family-check";
          const check = make("input");
          check.type = "checkbox";
          check.checked = data.selected.length > 0;
          label.append(check, document.createTextNode(" Send this P-Chart to my Band Director"));
          const chooseLabel = make("label", "Choose a director and Class");
          const choose = make("select");
          const id = `private-chart-recipient-${card.dataset.classroomChart}`;
          choose.id = id;
          chooseLabel.htmlFor = id;
          const blank = make("option", "Choose a director and Class");
          blank.value = "";
          choose.append(blank);
          for (const choice of data.choices) {
            const option = make("option", `${choice.director_name} · ${choice.class_name}`);
            option.value = String(choice.assignment_id);
            choose.append(option);
          }
          choose.value = data.selected.length ? String(data.selected[0]) : "";
          const save = make("button", "Save sharing");
          save.type = "submit";
          const status = make("p");
          status.setAttribute("role", "status");
          form.append(label, chooseLabel, choose, save, status);
          form.addEventListener("submit", async event => {
            event.preventDefault();
            if (check.checked && !choose.value) {
              status.textContent = "Choose a current director and Class.";
              return;
            }
            save.disabled = true;
            try {
              const result = await request(`/classroom/chart-sharing/${card.dataset.classroomChart}`, {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({csrf: data.csrf, assignment_ids: check.checked ? [Number(choose.value)] : []})
              });
              if (!result.ok) throw new Error("unavailable");
              status.textContent = "Sharing saved. Other P-Charts and comprehensive reporting remain separate.";
            } catch (_) {
              status.textContent = "Sharing was not saved. Reopen the current choices and check required permissions.";
            } finally { save.disabled = false; }
          });
          editor.replaceChildren(form);
        } catch (_) { editor.replaceChildren(make("p", "Sharing is currently unavailable for this P-Chart.")); }
      });
    }
  }).catch(() => {});
  window.addEventListener("pagehide", () => {
    for (const card of cards) card.querySelector("[data-chart-sharing-editor]").replaceChildren();
  });
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
})();
