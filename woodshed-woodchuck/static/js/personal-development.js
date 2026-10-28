(() => {
  "use strict";
  const root = document.querySelector("[data-personal-development]");
  if (!root) return;
  const book = root.dataset.personalDevelopment === "book";
  const content = root.querySelector("[data-pdd-content]");
  const status = root.querySelector("[data-pdd-status]");
  const invitation = root.querySelector("[data-pdd-invitation]");
  const enabled = root.dataset.enabled === "true";
  let generation = 0;
  let stopped = false;
  const number = value => new Intl.NumberFormat("en-US", {maximumFractionDigits: 2}).format(value);
  const duration = seconds => window.WWPracticeDuration.seconds(seconds);
  const axisNumber = value => new Intl.NumberFormat("en-US", {notation: "compact", maximumFractionDigits: 1}).format(value);
  const axisDay = value => new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", timeZone: "UTC"
  }).format(new Date(value + "T00:00:00Z"));
  const day = value => new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric", timeZone: "UTC"
  }).format(new Date(value + "T00:00:00Z"));
  const range = (start, end) => `${day(start)} – ${day(end)}`;
  const submitted = value => new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
    timeZone: "America/Chicago", timeZoneName: "short"
  }).format(new Date(value));
  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  }
  function svgNode(tag, attributes, text) {
    const result = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes || {})) result.setAttribute(key, value);
    if (text !== undefined) result.textContent = text;
    return result;
  }
  function table(headers, rows, caption) {
    const wrap = node("div", undefined, "pdd-table-wrap");
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", caption);
    const result = node("table");
    result.append(node("caption", caption));
    const head = node("thead"), tr = node("tr");
    for (const label of headers) {
      const th = node("th", label); th.scope = "col"; tr.append(th);
    }
    head.append(tr); result.append(head);
    const body = node("tbody");
    for (const values of rows) {
      const row = node("tr");
      values.forEach((value, i) => {
        const cell = node(i === 0 ? "th" : "td", String(value));
        if (i === 0) cell.scope = "row";
        row.append(cell);
      });
      body.append(row);
    }
    result.append(body); wrap.append(result);
    return wrap;
  }
  function chart(points, weekly) {
    const label = weekly ? "Practice minutes Monday through Sunday" : "Cumulative lifetime practice minutes";
    const svg = svgNode("svg", {viewBox: "0 0 620 290", role: "img", "aria-label": label, class: "pdd-chart"});
    svg.append(svgNode("title", {}, label + ". Exact values are available in the table below."));
    const left = 90, right = 600, top = 28, bottom = 228;
    const peak = points.reduce((max, p) => Math.max(max, p.seconds / 60), 1);
    const unit = 10 ** Math.floor(Math.log10(peak));
    const max = Math.ceil(peak / unit) * unit;
    const y = seconds => bottom - (seconds / 60 / max) * (bottom - top);
    for (let i = 0; i <= 4; i++) {
      const value = max * i / 4, height = bottom - i / 4 * (bottom - top);
      svg.append(svgNode("line", {x1: left, x2: right, y1: height, y2: height, class: "grid"}));
      svg.append(svgNode("text", {x: left - 10, y: height + 6, "text-anchor": "end"}, axisNumber(value)));
    }
    svg.append(svgNode("text", {x: left, y: 18}, "Minutes"));
    if (weekly) {
      const names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      points.forEach((point, i) => {
        const x = left + (i + .5) * (right - left) / 7;
        const bar = svgNode("rect", {x: x - 22, y: y(point.seconds), width: 44,
          height: bottom - y(point.seconds), rx: 3, class: "bar"});
        bar.append(svgNode("title", {}, `${day(point.date)}: ${duration(point.seconds)}`));
        svg.append(bar, svgNode("text", {x, y: bottom + 30, "text-anchor": "middle"}, names[i]));
      });
    } else if (points.length) {
      const timestamp = point => Date.parse(point.date + "T00:00:00Z");
      const first = timestamp(points[0]), span = Math.max(1, timestamp(points.at(-1)) - first);
      const x = point => left + (timestamp(point) - first) / span * (right - left);
      svg.append(svgNode("polyline", {class: "line", points: points.map(p => `${x(p)},${y(p.seconds)}`).join(" ")}));
      for (const point of points) {
        const dot = svgNode("circle", {cx: x(point), cy: y(point.seconds), r: 3, class: "dot"});
        dot.append(svgNode("title", {}, `${day(point.date)}: ${duration(point.seconds)} total`));
        svg.append(dot);
      }
      svg.append(svgNode("text", {x: left, y: bottom + 30}, axisDay(points[0].date)));
      svg.append(svgNode("text", {x: right, y: bottom + 30, "text-anchor": "end"}, axisDay(points.at(-1).date)));
    }
    return svg;
  }
  function chartCard(title, points, total, caption, weekly) {
    const card = node("section", undefined, "pdd-chart-card");
    card.setAttribute("aria-label", title);
    card.append(node("h2", title), node("p", `${number(total / 60)} minutes`, "pdd-chart-total"),
      node("p", caption, "pdd-caption"), chart(points, weekly));
    if (!points.length) card.append(node("p", "Your saved practice will appear here."));
    else {
      const details = node("details");
      details.append(node("summary", "View exact practice times"), table(["Date", weekly ? "Practice time" : "Cumulative time"],
        points.map(p => [day(p.date), duration(p.seconds)]), title));
      card.append(details);
    }
    return card;
  }
  function section(title, id) {
    const result = node("section", undefined, "pdd-section");
    result.id = id;
    result.setAttribute("aria-labelledby", id + "-heading");
    const heading = node("h2", title); heading.id = id + "-heading";
    result.append(heading);
    return result;
  }
  function disclosure(label, ...children) {
    const result = node("details");
    result.append(node("summary", label), ...children);
    return result;
  }
  function facts(items) {
    const list = node("dl", undefined, "pdd-facts");
    for (const [label, value] of items) {
      const pair = node("div"); pair.append(node("dt", label), node("dd", String(value))); list.append(pair);
    }
    return list;
  }
  function overview(data) {
    const result = section("Your practice at a glance", "pdd-overview");
    const grid = node("div", undefined, "pdd-summary-grid");
    for (const [key, label] of [["weekly", "This week"], ["lifetime", "Lifetime"]]) {
      const totals = data.totals[key];
      const card = node("section", undefined, "pdd-summary-card");
      card.append(node("h3", label), facts([
        ["Practice Charts", number(totals.charts)], ["Practice days", number(totals.days)],
        ["Verified practice", duration(totals.verified_seconds)], ["Pristine practice", duration(totals.pristine_seconds)]
      ]));
      grid.append(card);
    }
    result.append(node("p", `This week: ${range(data.week_start, data.week_end)}`, "pdd-caption"), grid);
    const rhythm = facts([
      ["Current verified streak", `${data.current_verified_streak} ${data.current_verified_streak === 1 ? "day" : "days"}`],
      ["First practice recorded", data.first_practice ? day(data.first_practice) : "No practice yet"],
      ["Latest practice recorded", data.last_practice ? day(data.last_practice) : "No practice yet"]
    ]);
    rhythm.classList.add("pdd-rhythm");
    result.append(rhythm, node("p", "The streak uses approved BOOK practice days ending today or yesterday. Reported and Pristine time still count in your personal totals.", "pdd-caption"));
    return result;
  }
  function weeklyChart(data) {
    const result = node("section", undefined, "pdd-chart-card");
    result.setAttribute("aria-label", "Weekly practice minutes");
    const label = node("label", "Choose a practice week", "pdd-week-picker");
    const select = node("select"); select.id = "pdd-week"; label.htmlFor = select.id;
    for (const week of data.history) {
      const option = node("option", range(week.week_start, week.week_end)); option.value = week.week_start;
      select.append(option);
    }
    label.append(select);
    const plot = node("div");
    const update = () => {
      const week = data.history.find(w => w.week_start === select.value);
      const card = chartCard("Weekly practice minutes", week.daily, week.total_seconds,
        `${range(week.week_start, week.week_end)} · Monday–Sunday`, true);
      card.firstElementChild.remove();
      plot.replaceChildren(...card.childNodes);
    };
    select.addEventListener("change", update); update();
    result.append(node("h2", "Weekly practice minutes"), label, plot);
    return result;
  }
  function history(data) {
    const result = section("Practice history", "pdd-history");
    result.append(node("p", "Explore past weeks or the charts you submitted. Empty weeks are included; totals use each chart’s practice date.", "pdd-caption"));
    const weeks = node("div"), controls = node("div", undefined, "pdd-pagination");
    let offset = 0;
    const previous = node("button", "Newer weeks"), next = node("button", "Older weeks"), label = node("span");
    previous.type = next.type = "button"; label.setAttribute("role", "status");
    const draw = () => {
      weeks.replaceChildren(table(["Week", "Practice", "Charts", "Days", "Verified", "Pristine"],
        data.history.slice(offset, offset + 12).map(w => [range(w.week_start, w.week_end), duration(w.total_seconds),
          w.charts, w.days, duration(w.verified_seconds), duration(w.pristine_seconds)]), "Weekly practice history"));
      previous.disabled = offset === 0; next.disabled = offset + 12 >= data.history.length;
      label.textContent = `Weeks ${offset + 1}–${Math.min(offset + 12, data.history.length)} of ${data.history.length}`;
    };
    previous.addEventListener("click", () => { offset -= 12; draw(); });
    next.addEventListener("click", () => { offset += 12; draw(); });
    controls.append(previous, label, next); draw();
    result.append(disclosure("Weekly totals", node("p", "Scroll sideways for all columns.", "pdd-table-hint"), controls, weeks), submissions(data.totals.lifetime.charts));
    return result;
  }
  function submissions(count) {
    const panel = disclosure(`Practice Chart submissions (${number(count)})`);
    panel.classList.add("pdd-submissions");
    const info = node("p", "Newest submissions first. Submission times use America/Chicago time; practice dates can differ.", "pdd-caption");
    const message = node("p"); message.setAttribute("role", "status");
    const list = node("ol", undefined, "pdd-submission-list");
    let page = 1, requestNumber = 0;
    const previous = node("button", "Newer submissions"), next = node("button", "Older submissions");
    previous.type = next.type = "button";
    const controls = node("div", undefined, "pdd-pagination"); controls.append(previous, next);
    async function load(target) {
      if (stopped || !panel.isConnected || document.hidden) return;
      const activeGeneration = generation, activeRequest = ++requestNumber;
      previous.disabled = next.disabled = true; list.replaceChildren();
      message.textContent = "Loading your submissions…";
      try {
        const response = await fetch(`/personal-development/charts?page=${target}`, {credentials: "same-origin", cache: "no-store"});
        const data = await response.json();
        if (stopped || activeGeneration !== generation || activeRequest !== requestNumber) return;
        if (!response.ok) {
          if (response.status === 401 || response.status === 403) { clear(); unavailable(data); }
          else message.textContent = "Submissions are temporarily unavailable. Close and reopen to try again.";
          return;
        }
        page = data.page;
        message.textContent = data.total ? `Page ${page} of ${Math.ceil(data.total / data.page_size)} · ${number(data.total)} saved charts` : "No Practice Charts saved yet.";
        list.start = (page - 1) * data.page_size + 1;
        for (const entry of data.charts) {
          const item = node("li");
          item.append(node("h3", day(entry.practice_date)),
            node("p", `${duration(entry.seconds)} · ${entry.kind}`, "pdd-submission-duration"),
            node("p", `Submitted ${submitted(entry.submitted_at)}`, "pdd-caption"),
            node("p", entry.categories.length ? entry.categories.join(" · ") : "No standard categories recorded"));
          list.append(item);
        }
        previous.disabled = page <= 1; next.disabled = page * data.page_size >= data.total;
      } catch (_) {
        if (activeGeneration === generation && activeRequest === requestNumber && !stopped)
          message.textContent = "Submissions are temporarily unavailable. Close and reopen to try again.";
      }
    }
    previous.addEventListener("click", () => load(page - 1)); next.addEventListener("click", () => load(page + 1));
    panel.addEventListener("toggle", () => { if (panel.open) load(1); });
    panel.append(info, message, controls, list);
    return panel;
  }
  function categories(data) {
    const result = section("What Did You Work On?", "pdd-categories");
    const activityTable = node("table", undefined, "pdd-activity-table");
    activityTable.id = "pdd-category-table";
    activityTable.setAttribute("aria-labelledby", "pdd-categories-heading");
    activityTable.setAttribute("aria-describedby", "pdd-category-explanation");
    const head = node("thead"), headings = node("tr"), name = node("th", "Practice Activity");
    name.scope = "col"; headings.append(name);
    const body = node("tbody"), columns = [];
    let showAll = false, sortKey = "weekly_charts", descending = true;
    for (const [key, label] of [["weekly_charts", "This Week"], ["lifetime_charts", "Lifetime"]]) {
      const heading = node("th"), button = node("button", undefined, "pdd-activity-sort");
      heading.scope = "col"; button.type = "button";
      const indicator = node("span"); indicator.setAttribute("aria-hidden", "true");
      button.append(node("span", label), indicator);
      button.addEventListener("click", () => {
        descending = sortKey === key ? !descending : true;
        sortKey = key; draw();
      });
      heading.append(button); headings.append(heading);
      columns.push({key, label, heading, button, indicator});
    }
    head.append(headings); activityTable.append(head, body);
    const toggle = node("button", undefined, "pdd-category-toggle"); toggle.type = "button";
    toggle.setAttribute("aria-controls", activityTable.id);
    const message = node("p", undefined, "pdd-caption"); message.setAttribute("role", "status");
    function draw() {
      // Sort a filtered copy only; server-calculated counts and other dashboard
      // data are untouched. Explicit column sorting applies to every shown row.
      const rows = data.categories.filter(c => showAll || c.weekly_charts > 0).sort((a, b) =>
        (descending ? b[sortKey] - a[sortKey] : a[sortKey] - b[sortKey]) ||
        b.weekly_charts - a.weekly_charts || b.lifetime_charts - a.lifetime_charts || a.name.localeCompare(b.name));
      body.replaceChildren();
      for (const category of rows) {
        const row = node("tr"), label = node("th", category.name); label.scope = "row";
        row.append(label, node("td", number(category.weekly_charts)), node("td", number(category.lifetime_charts)));
        body.append(row);
      }
      if (!rows.length) {
        const row = node("tr"), empty = node("td",
          "No activities were reported this week. Show all 23 categories to see your lifetime counts.", "pdd-category-empty");
        empty.colSpan = 3; row.append(empty); body.append(row);
      }
      for (const column of columns) {
        const selected = column.key === sortKey;
        if (selected) column.heading.setAttribute("aria-sort", descending ? "descending" : "ascending");
        else column.heading.removeAttribute("aria-sort");
        column.indicator.textContent = selected ? (descending ? "↓" : "↑") : "↕";
        column.button.setAttribute("aria-label", `${column.label}: sort ${selected && descending ? "ascending" : "descending"}`);
      }
      toggle.textContent = showAll ? "Collapse" : "Show All 23 Categories";
      toggle.setAttribute("aria-expanded", String(showAll));
      message.textContent = `${showAll ? "All 23 categories" : `${rows.length} ${rows.length === 1 ? "activity" : "activities"} reported this week`} · ${sortKey === "weekly_charts" ? "This Week" : "Lifetime"}, ${descending ? "highest first" : "lowest first"}`;
    }
    toggle.addEventListener("click", () => { showAll = !showAll; draw(); });
    const explanation = node("p", "Counts represent Practice Charts where an activity was selected—not minutes practiced or skill proficiency.", "pdd-caption");
    explanation.id = "pdd-category-explanation";
    draw();
    result.append(node("p", `This week: ${range(data.week_start, data.week_end)}`, "pdd-caption"),
      activityTable, explanation, toggle, message);
    return result;
  }
  function definitions() {
    return disclosure("About these statistics", node("p",
      "Personal totals include all your saved charts, shared or private, including contest opt-outs. Verified and Pristine are parts of total practice; Pristine is never counted twice. Verification reflects the chart’s current status, not a historical snapshot."), node("p",
      "Practice days require positive saved time. Chart counts include zero-minute records. The current verified streak follows the existing BOOK rule: consecutive positive BOOK practice days with an approved verification response, ending today or yesterday. Pristine records do not extend that streak."), node("p",
      "Weeks run Monday–Sunday in America/Chicago time. Practice Insights covers four completed weeks; your Practice Rating remains the last completed week even when you explore an older week. Category counts use saved selections; an absent selection does not mean you never practiced that skill."), node("p",
      "Time per category, skill mastery, and exercise-completion analytics are unavailable because these records do not contain that information."));
  }
  function render(data) {
    if (book) {
      content.textContent = data.rating.toFixed(1);
      status.textContent = `Last completed week · ${range(data.rating_week, data.rating_week_end)}`;
      return;
    }
    const rating = node("section", undefined, "pdd-rating-panel");
    const circle = node("div", data.rating.toFixed(1), "bd-rating");
    circle.setAttribute("aria-label", `Practice Rating ${data.rating.toFixed(1)}`);
    const description = node("div");
    description.append(node("h2", "Practice Rating"), node("p", "Your last completed week"),
      node("p", range(data.rating_week, data.rating_week_end), "pdd-rating-meta"));
    rating.append(circle, description);
    const jump = node("nav", undefined, "pdd-jump-links"); jump.setAttribute("aria-label", "Dashboard sections");
    for (const [id, title] of [["overview", "Overview"], ["insights", "Insights"], ["history", "History"], ["categories", "Categories"]]) {
      const link = node("a", title); link.href = `#pdd-${id}`; jump.append(link);
    }
    const charts = node("div", undefined, "director-chart-grid");
    charts.append(chartCard("Lifetime practice minutes", data.lifetime, data.lifetime_seconds,
      "Cumulative saved practice" + (data.lifetime.length ? ` · ${range(data.lifetime[0].date, data.lifetime.at(-1).date)}` : ""), false), weeklyChart(data));
    const insights = node("section", undefined, "pdd-insights");
    insights.id = "pdd-insights";
    insights.append(node("h2", "Practice Insights"), node("p", "Scroll sideways for all columns.", "pdd-table-hint"), table(
      ["Week", "Practice", "Days", "Verified", "Pristine"],
      data.insights.weeks.map(w => [range(w.week_start, w.week_end), duration(w.seconds),
        w.days, duration(w.verified_seconds), duration(w.pristine_seconds)]), "Last four completed weeks"),
      node("p", `Four-week total: ${duration(data.insights.total_seconds)} · Weekly average: ${duration(data.insights.average_weekly_seconds)}`));
    content.append(rating, jump, charts, overview(data), insights, history(data), categories(data), definitions(), node("p",
      "Your personal totals include saved charts whether or not you share them. Weeks follow America/Chicago time.", "pdd-caption"));
    content.hidden = false;
    status.textContent = "";
  }
  function clear() {
    generation++;
    content.replaceChildren();
    if (!book) content.hidden = true;
  }
  function unavailable(data) {
    if (data.detail?.code === "entitlement_required") {
      if (invitation) invitation.hidden = false;
      if (book) content.textContent = "View";
      status.textContent = book ? "Explore your practice progress." : "";
    } else status.textContent = "Your dashboard is unavailable. Return to BOOK or sign in again.";
  }
  async function refresh() {
    if (!enabled || stopped || document.hidden) return;
    clear();
    if (invitation) invitation.hidden = true;
    const request = generation;
    status.textContent = "Loading your practice progress…";
    try {
      const response = await fetch(`/personal-development/${book ? "rating" : "data"}`, {
        credentials: "same-origin", cache: "no-store"
      });
      const data = await response.json();
      if (request !== generation || stopped) return;
      if (!response.ok) {
        unavailable(data);
        return;
      }
      render(data);
    } catch (_) {
      if (request === generation && !stopped) status.textContent = "Your dashboard is temporarily unavailable. Reload to try again.";
    }
  }
  window.addEventListener("pagehide", clear);
  window.addEventListener("ww:session-changed", () => { stopped = true; clear(); status.textContent = "Sign-in changed. Reload to continue."; });
  document.addEventListener("visibilitychange", () => { if (document.hidden) clear(); else refresh(); });
  window.addEventListener("pageshow", event => { if (event.persisted) refresh(); });
  refresh();
})();
