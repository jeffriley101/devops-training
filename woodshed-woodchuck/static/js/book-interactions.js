(function () {
  "use strict";
  const addDays = (date, days) => {
    const value = new Date(date + "T00:00:00Z"); value.setUTCDate(value.getUTCDate() + days);
    return value.toISOString().slice(0, 10);
  };
  const monday = date => addDays(date, -((new Date(date + "T00:00:00Z").getUTCDay() + 6) % 7));
  function aggregate(charts, weekStart) {
    const days = new Map(), weeks = new Map();
    for (const chart of charts) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(chart.practice_date) || !Number.isFinite(chart.duration_seconds) || chart.duration_seconds < 0) throw new Error("Saved chart data is unavailable.");
      const date = chart.practice_date, week = monday(date);
      days.set(date, (days.get(date) || 0) + chart.duration_seconds);
      weeks.set(week, (weeks.get(week) || 0) + 1);
    }
    const weekKeys = [...weeks.keys()].sort();
    if (weekKeys.length) for (let date = weekKeys[0]; date <= weekKeys.at(-1); date = addDays(date, 7)) {
      if (!weeks.has(date)) weeks.set(date, 0);
    }
    let cumulative = 0;
    const career = [...days].sort(([a], [b]) => a.localeCompare(b)).map(([date, seconds]) => ({date, value: cumulative += seconds}));
    return {days, career, weeks: [...weeks].sort(([a], [b]) => a.localeCompare(b)).map(([date, value]) => ({date, value})),
      week: Array.from({length: 7}, (_, i) => { const date = addDays(weekStart, i); return {date, value: days.get(date) || 0}; }),
      totalSeconds: cumulative, totalDays: [...days.values()].filter(seconds => seconds > 0).length, totalCharts: charts.length};
  }
  window.WWBookStats = Object.freeze({aggregate, monday});
  if (!document.querySelector(".p-book-page")) return;

  for (const group of document.querySelectorAll("[data-method-help]")) {
    const help = group.querySelector('[role="tooltip"]'), info = group.querySelector(".p-book-info");
    let pinned = false, suppressed = false;
    function show(open) { help.hidden = !open; info.setAttribute("aria-expanded", String(open)); }
    group.addEventListener("pointerenter", () => { if (!suppressed) show(true); });
    group.addEventListener("pointerleave", () => { suppressed = false; if (!pinned) show(false); });
    group.addEventListener("focusin", () => { if (!suppressed) show(true); });
    group.addEventListener("focusout", event => { if (!group.contains(event.relatedTarget)) { pinned = false; suppressed = false; show(false); } });
    info.addEventListener("click", () => { pinned = !pinned; suppressed = !pinned; show(pinned); });
    document.addEventListener("keydown", event => { if (event.key === "Escape" && !help.hidden) { pinned = false; suppressed = true; show(false); } });
    document.addEventListener("pointerdown", event => { if (!group.contains(event.target)) { pinned = false; show(false); } });
  }

  const dialog = document.getElementById("p-book-stat-dialog"), content = dialog.querySelector("[data-book-stat-content]");
  const status = dialog.querySelector("[data-book-stat-status]"), title = document.getElementById("p-book-stat-title");
  let opener = null, generation = 0;
  const node = (tag, text, className) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; };
  const time = seconds => window.WWPracticeDuration.seconds(seconds);
  function table(points, count = false, cumulative = false) {
    const wrap = node("div", undefined, "p-book-chart-table");
    wrap.tabIndex = 0; wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", "Exact saved values");
    const result = node("table"), caption = node("caption", "Exact saved values"), head = node("thead"), row = node("tr");
    for (const text of ["Date / week", count ? "P-Charts" : cumulative ? "Cumulative time" : "Practice time"]) { const cell = node("th", text); cell.scope = "col"; row.append(cell); }
    head.append(row); result.append(caption, head);
    const body = node("tbody");
    for (const point of points) { const row = node("tr"), date = node("th", point.date); date.scope = "row"; row.append(date, node("td", count ? String(point.value) : time(point.value))); body.append(row); }
    result.append(body); wrap.append(result); return wrap;
  }
  function plot(points, line = false, count = false) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 400 220"); svg.classList.add("p-book-chart"); svg.setAttribute("aria-hidden", "true");
    const shape = (tag, attributes, text) => { const el = document.createElementNS(svg.namespaceURI, tag); for (const [k,v] of Object.entries(attributes)) el.setAttribute(k,v); if (text !== undefined) el.textContent = text; return el; };
    const max = Math.max(1, ...points.map(p => p.value)), bottom = 180;
    const x = i => 40 + (i + .5) * 340 / Math.max(1, points.length), y = value => bottom - value / max * 140;
    svg.append(shape("line", {x1:40,x2:390,y1:bottom,y2:bottom,class:"grid"}));
    svg.append(shape("text", {x:4,y:24}, count ? `${max} charts` : time(max)), shape("text", {x:5,y:bottom}, "0"));
    if (line && points.length) {
      // Position points by their actual dates, rather than treating missing dates as sessions.
      const first = Date.parse(points[0].date), span = Math.max(1, Date.parse(points.at(-1).date) - first);
      const px = p => 40 + (Date.parse(p.date) - first) / span * 340;
      svg.append(shape("polyline", {points: points.flatMap((p,i) => i ? [`${px(p)},${y(points[i-1].value)}`, `${px(p)},${y(p.value)}`] : [`${px(p)},${y(p.value)}`]).join(" "), class:"line"}));
      for (const p of points) svg.append(shape("circle", {cx:px(p),cy:y(p.value),r:3,class:"dot"}));
    } else points.forEach((p,i) => svg.append(shape("rect", {x:x(i)-Math.min(18,140/Math.max(1,points.length)),y:y(p.value),width:Math.min(36,280/Math.max(1,points.length)),height:bottom-y(p.value),class:"bar"})));
    if (points.length === 7 && !count && !line) points.forEach((p,i) => svg.append(shape("text", {x:x(i),y:210,"text-anchor":"middle"}, ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][i])));
    else if (points.length) svg.append(shape("text", {x:40,y:210}, points[0].date), shape("text", {x:390,y:210,"text-anchor":"end"}, points.at(-1).date));
    return svg;
  }
  function calendar(data, today) {
    const label = node("label", "Calendar month"), select = node("select"); select.setAttribute("aria-label", "Calendar month");
    const months = new Set([today.slice(0,7), ...[...data.days.keys()].map(date => date.slice(0,7))]);
    for (const month of [...months].sort().reverse()) { const option = node("option", month); option.value = month; select.append(option); }
    select.value = today.slice(0,7); label.append(select);
    const region = node("div"), draw = () => {
      const month = select.value, grid = node("div", undefined, "p-book-calendar");
      const points = [];
      for (const name of ["M","T","W","T","F","S","S"]) grid.append(node("span", name));
      const first = month + "-01", offset = (new Date(first + "T00:00:00Z").getUTCDay()+6)%7;
      for (let i=0;i<offset;i++) grid.append(node("span", ""));
      for (let date=first;date.startsWith(month);date=addDays(date,1)) {
        const seconds = data.days.get(date) || 0, cell = node("span", String(Number(date.slice(8))), seconds > 0 ? "has-practice" : "");
        cell.setAttribute("aria-label", `${date}: ${time(seconds)}`); grid.append(cell); points.push({date,value:seconds});
      }
      region.replaceChildren(node("p", `${month} only · Green days have positive saved practice time.`), grid, table(points));
    };
    select.addEventListener("change", draw); draw(); content.append(label, region);
  }
  const headings = {week:"This Week’s Reported Practice",career:"Career Reported Practice",days:"Saved Practice Days",charts:"Saved Charts"};
  async function open(button) {
    opener = button; const request = ++generation, binding = window.WWSessionBoundary?.binding();
    title.textContent = headings[button.dataset.bookStat]; content.replaceChildren(); status.textContent = "Loading saved practice…";
    dialog.showModal(); dialog.querySelector("[data-book-stat-close]").focus();
    try {
      const [history, totals] = await Promise.all([fetch("/practice-charts", {credentials:"same-origin",cache:"no-store"}),fetch("/practice-charts/totals", {credentials:"same-origin",cache:"no-store"})]);
      if (request !== generation || !dialog.open || !window.WWSessionBoundary?.matchesBinding(binding)) return;
      if (!history.ok || !totals.ok) throw new Error("Your saved practice is unavailable. Close this chart and sign in again or retry.");
      const [rows, period] = await Promise.all([history.json(),totals.json()]);
      if (request !== generation || !dialog.open || !window.WWSessionBoundary.matchesBinding(binding)) return;
      const data = aggregate(rows.charts, period.week_start), key = button.dataset.bookStat;
      if (data.totalSeconds !== period.career_seconds || data.week.reduce((sum,p) => sum+p.value,0) !== period.this_week_seconds) throw new Error("Saved totals changed while loading. Close and reopen this chart to refresh.");
      status.textContent = "";
      if (key === "days") { content.append(node("p", `${data.totalDays} practice days across all saved history.`)); calendar(data, window.WWState.localDateKey()); return; }
      const count = key === "charts", points = key === "week" ? data.week : count ? data.weeks : data.career;
      content.append(node("p", key === "week" ? `${period.week_start} – ${period.week_end} · Monday–Sunday · ${time(period.this_week_seconds)}` : count ? `${data.totalCharts} P-Charts across all saved history. Counts are charts, not distinct sessions.` : `${time(data.totalSeconds)} across all saved history. Cumulative reported time; overlapping charts may credit the same practice.`));
      if (points.length) content.append(plot(points,key === "career",count),table(points,count,key === "career"));
      else content.append(node("p", "No saved practice charts yet."));
    } catch (error) { if (request === generation) { content.replaceChildren(); status.textContent = error.message || "Chart unavailable. Close and retry."; } }
  }
  function clear() { generation++; content.replaceChildren(); status.textContent = ""; }
  dialog.querySelector("[data-book-stat-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { clear(); opener?.focus({preventScroll:true}); });
  for (const button of document.querySelectorAll("[data-book-stat]")) button.addEventListener("click", () => open(button));
  window.addEventListener("ww:session-changed", () => { clear(); if (dialog.open) dialog.close(); });
  window.addEventListener("pagehide", () => { clear(); if (dialog.open) dialog.close(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { clear(); if (dialog.open) dialog.close(); } });
})();
