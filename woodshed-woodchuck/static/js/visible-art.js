/* Progressive enhancement for the current season's picture. No saved state. */
(() => {
  "use strict";
  const trigger = document.querySelector("[data-season-art-open]");
  const dialog = document.getElementById("season-art-dialog");
  if (!trigger || !dialog || typeof dialog.showModal !== "function") return;

  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.setAttribute("aria-controls", dialog.id);
  trigger.addEventListener("click", (event) => {
    // Modified clicks keep the ordinary image-link behavior.
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    dialog.hidden = false;
    try {
      dialog.showModal();
      event.preventDefault();
    } catch {
      dialog.hidden = true; // The href still works if modal opening is unavailable.
    }
  });
  // Native Escape and the method="dialog" close button both arrive here.
  dialog.addEventListener("close", () => {
    dialog.hidden = true;
    trigger.focus({ preventScroll: true });
  });
})();
