(() => {
  const page = document.querySelector(
    "#trusted-verifier-accept-page"
  );
  const form = document.querySelector(
    "#trusted-verifier-accept-form"
  );

  if (!page || !form) {
    return;
  }

  const token = page.dataset.invitationToken;
  const errorText = document.querySelector(
    "#trusted-verifier-accept-error"
  );
  const successPanel = document.querySelector(
    "#trusted-verifier-accept-success"
  );
  const successMessage = document.querySelector(
    "#trusted-verifier-accept-message"
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    errorText.textContent = "";
    successPanel.hidden = true;

    try {
      const response = await fetch(
        `/trusted-verifiers/invitations/${encodeURIComponent(
          token
        )}/accept`,
        {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
        }
      );

      const payload = await response.json();

      if (!response.ok) {
        throw new Error(
          payload.detail || "Could not accept the invitation."
        );
      }

      successMessage.textContent =
        `${payload.verifier.display_name} is now connected as ` +
        `${payload.connection.role.replaceAll("_", " ")}.`;

      successPanel.hidden = false;
      form.hidden = true;
    } catch (error) {
      errorText.textContent =
        error.message || "Could not accept the invitation.";
    }
  });
})();
