/* QR supplies the same form values; it never submits or authorizes a join. */
(function () {
  "use strict";
  const signedIn = document.body.dataset.enrollmentSignedIn === "true";
  const form = document.getElementById("enrollment-code-form");
  const status = document.getElementById("enrollment-qr-status");
  function readQR() {
    if (!signedIn || !form || !location.hash) return;
    const fragment = location.hash.slice(1);
    history.replaceState(null, "", location.pathname);
    let valid = false;
    try {
      const values = new URLSearchParams(fragment);
      const entries = [...values.entries()];
      const program = values.get("program") || "";
      const code = values.get("code") || "";
      valid = fragment.length <= 256 && entries.length === 2 &&
        entries.every(([key]) => key === "program" || key === "code") &&
        values.getAll("program").length === 1 && values.getAll("code").length === 1 &&
        /^[1-9][0-9]{0,9}$/.test(program) && Number(program) <= 2147483647 &&
        /^[A-Za-z0-9]{6,24}$/.test(code);
      if (valid) {
        form.elements.program_id.value = program;
        form.elements.code.value = code;
        status.textContent = "Class QR loaded. Review the Class before confirming enrollment.";
        form.querySelector("button").focus();
      }
    } catch (_) { valid = false; }
    if (!valid) status.textContent = "This QR is not a supported Class entry. Enter the current details from your director.";
  }
  readQR();
  window.addEventListener("hashchange", readQR);
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
  window.addEventListener("pagehide", () => {
    document.querySelectorAll("[data-enrollment-private]").forEach(node => node.replaceChildren());
    document.querySelectorAll('input[type="password"]').forEach(node => { node.value = ""; });
  });
})();
