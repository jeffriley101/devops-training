(() => {
  const table = document.querySelector("[data-director-table]");
  if (!table) return;
  const headers = Array.from(table.querySelectorAll("thead th"));
  const body = table.tBodies[0];
  const feedback = document.querySelector("[data-sort-feedback]");
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
