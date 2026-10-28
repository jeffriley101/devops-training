/* Live, explicitly shared practice only. No ratings, roster inference, or browser storage. */
(() => {
  "use strict";
  const $ = id => document.getElementById(`v3-${id}`);
  const source = $("dashboard-data");
  if (!source) return;
  const status = $("status"), view = $("data-view"), dialog = $("detail");
  let data;
  try { data = JSON.parse(source.textContent); }
  catch (_) { status.textContent = "Shared practice could not be loaded. Reopen this class."; source.remove(); return; }
  source.remove();
  const node = (tag, text, cls) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (cls) item.className = cls;
    return item;
  };
  const number = value => new Intl.NumberFormat("en-US", {maximumFractionDigits: 2}).format(value);
  const addDays = (day, offset) => {
    const value = new Date(`${day}T12:00:00Z`);
    value.setUTCDate(value.getUTCDate() + offset);
    return value.toISOString().slice(0, 10);
  };
  const calendarDate = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC", month: "long", day: "numeric", year: "numeric"
  });
  const timestamp = value => value ? new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago", dateStyle: "medium", timeStyle: "short"
  }).format(new Date(value)) + " CT" : "—";
  const fields = [
    {id: "instruments", label: "Shared chart instruments", group: "Shared practice", text: true},
    {id: "total", label: "Weekly minutes", group: "Shared practice", default: true},
    {id: "life-total", label: "Shared history minutes", group: "Shared practice", default: true},
    {id: "charts", label: "Weekly charts", group: "Shared practice", default: true},
    {id: "life-charts", label: "Shared history charts", group: "Shared practice"},
    {id: "days", label: "Weekly practice days", group: "Shared practice"},
    {id: "life-days", label: "Shared history practice days", group: "Shared practice"},
    {id: "verified", label: "Weekly Verified minutes", group: "Shared practice", default: true},
    {id: "pristine", label: "Weekly Pristine minutes", group: "Shared practice", default: true},
    {id: "life-verified", label: "Shared history Verified minutes", group: "Shared practice"},
    {id: "life-pristine", label: "Shared history Pristine minutes", group: "Shared practice"},
    {id: "submissions", label: "P-BOOK submissions · Displayed week", group: "Submissions"},
    {id: "last_submission", label: "Last P-BOOK submission", group: "Submissions", text: true, default: true},
    ...data.categories.map((name, index) => ({id: `category-${index}`, label: name, group: "What Did You Work On?", category: index}))
  ];
  const selected = new Set(data.students.map(student => student.key));
  const shown = new Set(fields.filter(field => field.default).map(field => field.id));
  let sort = {key: "name", direction: 1}, detailStudent = null, chartPage = 0;
  const week = () => $("week").value;
  const weekly = student => student.weekly[week()] || {};
  const selectedStudents = () => data.students.filter(student => selected.has(student.key));
  const value = (student, field) => {
    if (field.id === "name") return student.name;
    if (field.id === "instruments") return student.instruments.join(", ");
    if (field.id === "last_submission") return student.last_submission || "";
    if (field.category !== undefined) return Number((weekly(student).categories || [])[field.category] > 0);
    return field.id.startsWith("life-") ? student.lifetime[field.id.slice(5)] : (weekly(student)[field.id] || 0);
  };
  const formatted = (student, field) => {
    const result = value(student, field);
    if (field.id === "last_submission") return timestamp(result);
    if (field.category !== undefined) return result ? "Reported" : "—";
    return field.text ? result : number(result);
  };
  const svgNode = (tag, attributes, text) => {
    const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, val]) => item.setAttribute(key, String(val)));
    if (text !== undefined) item.textContent = text;
    return item;
  };
  function points(students, kind) {
    if (kind === "weekly") return Array.from({length: 7}, (_, day) => ({
      date: addDays(week(), day), seconds: students.reduce((sum, student) => sum + ((weekly(student).daily_seconds || [])[day] || 0), 0)
    }));
    const days = new Map();
    students.forEach(student => student.history.forEach(day => days.set(day.date, (days.get(day.date) || 0) + day.seconds)));
    let cumulative = 0;
    const history = [];
    // Match the personal dashboard's sparse calendar presentation: preserve
    // flat gaps instead of implying practice between distant recorded days.
    [...days.entries()].sort(([a], [b]) => a.localeCompare(b)).forEach(([date, seconds]) => {
      const previous = addDays(date, -1);
      if (!history.length || previous > history.at(-1).date) history.push({date: previous, seconds: cumulative});
      cumulative += seconds; history.push({date, seconds: cumulative});
    });
    return history;
  }
  function graph(svg, values, kind) {
    svg.replaceChildren();
    svg.setAttribute("viewBox", "0 0 360 150");
    svg.append(svgNode("title", {}, kind === "weekly" ? "Shared practice minutes Monday through Sunday" : "Cumulative minutes from currently shared charts"));
    if (!values.length) { svg.append(svgNode("text", {x: 15, y: 75}, "No shared practice to plot")); return; }
    const max = Math.max(60, ...values.map(point => point.seconds));
    const y = seconds => 118 - seconds / max * 94;
    [0, max / 2, max].forEach(tick => {
      svg.append(svgNode("line", {x1: 40, x2: 350, y1: y(tick), y2: y(tick), class: "grid"}),
        svgNode("text", {x: 35, y: y(tick) + 4, "text-anchor": "end"}, number(tick / 60)));
    });
    if (kind === "weekly") values.forEach((point, i) => {
      const bar = svgNode("rect", {x: 47 + i * 43, y: y(point.seconds), width: 25, height: 118 - y(point.seconds), class: "bar"});
      bar.append(svgNode("title", {}, `${point.date}: ${number(point.seconds / 60)} minutes`));
      svg.append(bar, svgNode("text", {x: 59 + i * 43, y: 140, "text-anchor": "middle"}, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i]));
    });
    else {
      const first = Date.parse(values[0].date), span = Date.parse(values.at(-1).date) - first;
      const x = point => span ? 48 + (Date.parse(point.date) - first) / span * 294 : 195;
      svg.append(svgNode("polyline", {class: "line", points: values.map(point => `${x(point)},${y(point.seconds)}`).join(" ")}));
      values.forEach(point => {
        const dot = svgNode("circle", {cx: x(point), cy: y(point.seconds), r: 3, class: "dot"});
        dot.append(svgNode("title", {}, `${point.date}: ${number(point.seconds / 60)} cumulative minutes`));
        svg.append(dot);
      });
      svg.append(svgNode("text", {x: 42, y: 140}, values[0].date),
        svgNode("text", {x: 350, y: 140, "text-anchor": "end"}, values.at(-1).date));
    }
  }
  function dataTable(headings, rows, caption) {
    const table = node("table", undefined, "v3-small-table"), head = node("thead"), body = node("tbody"), top = node("tr");
    table.append(node("caption", caption));
    headings.forEach(text => { const cell = node("th", text); cell.scope = "col"; top.append(cell); });
    head.append(top);
    rows.forEach(row => { const tr = node("tr"); row.forEach((text, i) => { const cell = node(i ? "td" : "th", text); if (!i) cell.scope = "row"; tr.append(cell); }); body.append(tr); });
    table.append(head, body);
    return table;
  }
  function graphs(students, historyId, weeklyId, valuesId) {
    const history = points(students, "history"), daily = points(students, "weekly");
    graph($(historyId), history, "history"); graph($(weeklyId), daily, "weekly");
    const container = $(valuesId);
    container.replaceChildren();
    container.className = "v3-graph-values";
    container.append(dataTable(["Practice date", "Minutes"], daily.map(point => [point.date, number(point.seconds / 60)]), "Weekly shared minutes"),
      dataTable(["Practice date", "Cumulative minutes"], history.map(point => [point.date, number(point.seconds / 60)]), "Shared history"));
  }
  function sortedStudents() {
    const field = sort.key === "name" ? {id: "name", text: true} : fields.find(item => item.id === sort.key);
    return data.students.filter(student => !$("selected-only").checked || selected.has(student.key)).sort((a, b) => {
      const av = value(a, field), bv = value(b, field);
      const difference = field.text ? av.localeCompare(bv) : av - bv;
      return difference * sort.direction || a.name.localeCompare(b.name) || Number(a.key) - Number(b.key);
    });
  }
  function heading(field, name = false) {
    const th = node("th", undefined, name ? "v3-name" : undefined), button = node("button", field.label);
    th.scope = "col";
    th.setAttribute("aria-sort", sort.key === field.id ? (sort.direction === 1 ? "ascending" : "descending") : "none");
    button.id = `v3-sort-${field.id}`; button.type = "button";
    button.append(node("span", sort.key === field.id ? (sort.direction === 1 ? " ↑" : " ↓") : " ↕"));
    button.addEventListener("click", () => { sort = {key: field.id, direction: sort.key === field.id ? -sort.direction : 1}; render(); });
    th.append(button); return th;
  }
  function render() {
    if (!data) return;
    const focused = document.activeElement.id, chosen = selectedStudents(), columns = fields.filter(field => shown.has(field.id));
    $("history-total").textContent = `${number(chosen.reduce((sum, student) => sum + student.lifetime.total_seconds, 0) / 60)} min`;
    $("week-total").textContent = `${number(chosen.reduce((sum, student) => sum + (weekly(student).total_seconds || 0), 0) / 60)} min`;
    $("selection").textContent = `${chosen.length} of ${data.students.length} shared students selected`;
    $("submissions").textContent = `${chosen.reduce((sum, student) => sum + (weekly(student).submissions || 0), 0)} shared P-BOOK charts submitted in the displayed Central calendar week by the selected students. This is a count, not a submission rate.`;
    const empty = $("empty");
    empty.hidden = chosen.some(student => (weekly(student).charts || 0) > 0);
    empty.textContent = !data.students.length ? "No P-Charts are currently shared with you in this Class." : !chosen.length ? "No students selected. Select shared students to view their totals." : "No currently shared charts record practice in the displayed week. Other practice may be private.";
    graphs(chosen, "history-graph", "week-graph", "graph-values");
    const head = node("tr"); head.append(heading({id: "name", label: "Student"}, true)); columns.forEach(field => head.append(heading(field)));
    $("sheet-head").replaceChildren(head);
    const body = $("sheet-body"); body.replaceChildren();
    sortedStudents().forEach(student => {
      const row = node("tr"), name = node("th", undefined, "v3-name"), label = node("label"), checkbox = node("input"), button = node("button", student.name, "v3-student");
      name.scope = "row"; checkbox.type = "checkbox"; checkbox.id = `v3-select-${student.key}`; checkbox.checked = selected.has(student.key);
      checkbox.setAttribute("aria-label", `Include ${student.name} in shared totals`);
      checkbox.addEventListener("change", () => { if (checkbox.checked) selected.add(student.key); else selected.delete(student.key); render(); });
      label.append(checkbox); button.type = "button"; button.id = `v3-student-${student.key}`; button.setAttribute("aria-haspopup", "dialog");
      button.addEventListener("click", () => openDetail(student)); name.append(label, button); row.append(name);
      columns.forEach(field => row.append(node("td", formatted(student, field)))); body.append(row);
    });
    const foot = node("tr"), title = node("th", "Selected shared totals", "v3-name"); title.scope = "row"; foot.append(title);
    columns.forEach(field => {
      const cell = node("td", field.text ? "—" : number(chosen.reduce((sum, student) => sum + value(student, field), 0)));
      if (field.id.endsWith("days")) cell.title = "Sum of each selected student's shared practice days (student-days)";
      if (field.category !== undefined) cell.title = "Selected students who reported this activity on a shared chart in the displayed week";
      foot.append(cell);
    });
    $("sheet-foot").replaceChildren(foot);
    if (focused) document.getElementById(focused)?.focus({preventScroll: true});
  }
  function chartHistory() {
    const charts = detailStudent.charts, container = $("detail-charts");
    container.replaceChildren();
    charts.slice(chartPage * 25, (chartPage + 1) * 25).forEach(chart => {
      const article = node("article");
      article.append(node("h4", `${chart.date} · ${number(chart.seconds / 60)} min · ${chart.verification}`),
        node("p", `${chart.instrument} · ${chart.source === "pristine" ? "Recorded" : "Submitted"}: ${timestamp(chart.submitted_at)}`));
      if (chart.categories.length) article.append(node("p", chart.categories.join(", ")));
      if (chart.note) article.append(node("p", chart.note));
      container.append(article);
    });
    $("charts-page").textContent = `Page ${chartPage + 1} of ${Math.max(1, Math.ceil(charts.length / 25))} · ${charts.length} shared charts`;
    $("charts-prev").disabled = chartPage === 0; $("charts-next").disabled = (chartPage + 1) * 25 >= charts.length;
  }
  function openDetail(student) {
    detailStudent = student; chartPage = 0;
    $("detail-title").textContent = student.name;
    $("detail-week").textContent = `Displayed week: ${calendarDate.formatRange(
      new Date(`${week()}T12:00:00Z`), new Date(`${addDays(week(), 6)}T12:00:00Z`))} · America/Chicago`;
    const stats = $("detail-stats"); stats.replaceChildren();
    fields.filter(field => !field.text && field.category === undefined).forEach(field => {
      const item = node("div"); item.append(node("span", field.label), node("strong", formatted(student, field))); stats.append(item);
    });
    graphs([student], "detail-history", "detail-weekly", "detail-values");
    $("detail-categories").replaceChildren(dataTable(["Practice activity", "Displayed week", "Shared history"], data.categories.map((category, i) => [category, String((weekly(student).categories || [])[i] || 0), String(student.categories[i])]), "Counts of shared charts where each activity was selected; not minutes or proficiency."));
    chartHistory(); dialog.showModal();
  }
  data.weeks.forEach(start => { const option = node("option", `${start} – ${addDays(start, 6)}`); option.value = start; $("week").append(option); });
  $("week").value = data.current_week;
  const groups = new Map();
  fields.forEach(field => {
    if (!groups.has(field.group)) { const group = node("fieldset"); group.append(node("legend", field.group)); groups.set(field.group, group); $("column-groups").append(group); }
    const label = node("label"), checkbox = node("input"); checkbox.type = "checkbox"; checkbox.checked = shown.has(field.id);
    checkbox.addEventListener("change", () => { if (checkbox.checked) shown.add(field.id); else shown.delete(field.id); render(); });
    label.append(checkbox, node("span", field.label)); groups.get(field.group).append(label);
  });
  $("week").addEventListener("change", render);
  $("selected-only").addEventListener("change", render);
  $("select-all").addEventListener("click", () => { data.students.forEach(student => selected.add(student.key)); render(); });
  $("select-none").addEventListener("click", () => { selected.clear(); render(); });
  $("close-detail").addEventListener("click", () => dialog.close());
  $("charts-prev").addEventListener("click", () => { chartPage--; chartHistory(); });
  $("charts-next").addEventListener("click", () => { chartPage++; chartHistory(); });
  function clear() {
    if (!data) return;
    dialog.close(); data = null; detailStudent = null; selected.clear();
    view.replaceChildren(); view.hidden = true;
    status.textContent = "For privacy, this view was cleared. Reopen this class and confirm your PIN to check current permissions.";
  }
  window.addEventListener("pagehide", clear);
  window.addEventListener("ww:session-changed", clear);
  document.addEventListener("visibilitychange", () => { if (document.hidden) clear(); });
  status.textContent = `Loaded ${timestamp(data.as_of)}. Current sharing permissions are checked each time you open this class.`;
  view.hidden = false; render();
})();
