/* Printed credentials go to server review before student confirmation. */
(function () {
  "use strict";
  const signedIn = document.body.dataset.enrollmentSignedIn === "true";
  const form = document.getElementById("enrollment-printed-form");
  const status = document.getElementById("enrollment-qr-status");
  function readQR() {
    if (!signedIn || !form) return;
    form.hidden = true;
    form.elements.printed.value = "";
    if (!location.hash) return;
    const fragment = location.hash.slice(1);
    history.replaceState(null, "", location.pathname);
    let valid = false;
    try {
      if (fragment.length > 325) throw new Error();
      const values = new URLSearchParams(fragment);
      const token = values.get("qr") || "";
      valid = [...values].length === 1 && /^[A-Za-z0-9_-]{1,256}\.[0-9a-f]{64}$/.test(token);
      form.hidden = !valid;
      form.elements.printed.value = valid ? token : "";
      if (valid) {
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
