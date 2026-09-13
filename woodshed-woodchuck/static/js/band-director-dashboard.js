(() => {
  const table = document.querySelector("[data-director-table]");
  if (!table) return;
  const headers = Array.from(table.querySelectorAll("thead th"));
  const body = table.tBodies[0];
  const feedback = document.querySelector("[data-sort-feedback]");
  const search = document.querySelector("#bd-name-search");
  const team = document.querySelector("#bd-team-filter");
  const filterFeedback = document.querySelector("[data-filter-feedback]");
  function filterRows() {
    const query = (search?.value || "").trim().toLocaleLowerCase();
    const selectedTeam = team?.value || "";
    let shown = 0;
    for (const row of body.rows) {
      row.hidden = !row.cells[0].dataset.sortValue.toLocaleLowerCase().includes(query)
        || (selectedTeam !== "" && row.cells[row.cells.length - 1].dataset.sortValue !== selectedTeam);
      if (!row.hidden) shown++;
    }
    if (filterFeedback) filterFeedback.textContent = shown
      ? `Showing ${shown} of ${body.rows.length} students.` : "No students match these filters.";
  }
  search?.addEventListener("input", filterRows);
  team?.addEventListener("change", filterRows);
  filterRows();
  headers.forEach((header, index) => {
    const button = header.querySelector("button");
    button.addEventListener("click", () => {
      const direction = header.getAttribute("aria-sort") === "ascending" ? -1 : 1;
      const rows = Array.from(body.rows);
      rows.sort((a, b) => {
        const left = a.cells[index].dataset.sortValue;
        const right = b.cells[index].dataset.sortValue;
        return direction * (button.dataset.sort === "number"
          ? Number(left) - Number(right)
          : left.localeCompare(right, undefined, { sensitivity: "base" }));
      });
      body.append(...rows);
      headers.forEach((other) => other.setAttribute("aria-sort", "none"));
      const label = direction === 1 ? "ascending" : "descending";
      header.setAttribute("aria-sort", label);
      if (feedback) feedback.textContent = `Sorted by ${button.textContent.trim()}, ${label}.`;
    });
  });
})();
