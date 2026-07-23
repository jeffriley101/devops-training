(() => {
  const form = document.querySelector(
    "#trusted-verifier-login-form"
  );

  if (!form) {
    return;
  }

  const errorText = document.querySelector(
    "#trusted-verifier-login-error"
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorText.textContent = "";

    try {
      const response = await fetch(
        "/trusted-verifiers/login",
        {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
        }
      );

      const payload = await response.json();

      if (!response.ok) {
        throw new Error(
          payload.detail ||
          "Verifier email or PIN was not recognized."
        );
      }

      window.location.assign(
        "/trusted-verifiers/dashboard"
      );
    } catch (error) {
      errorText.textContent =
        error.message || "Could not sign in.";
    }
  });
})();
