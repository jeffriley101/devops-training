(() => {
  const form = document.querySelector(
    "#trusted-verifier-invite-form"
  );

  if (!form) {
    return;
  }

  const errorText = document.querySelector(
    "#trusted-verifier-invite-error"
  );
  const successPanel = document.querySelector(
    "#trusted-verifier-invite-success"
  );
  const inviteLink = document.querySelector(
    "#trusted-verifier-invite-link"
  );
  const copyButton = document.querySelector(
    "#trusted-verifier-copy-link"
  );
  const copyFeedback = document.querySelector(
    "#trusted-verifier-copy-feedback"
  );
  const connectionList = document.querySelector(
    "#trusted-verifier-connection-list"
  );
  const invitationList = document.querySelector(
    "#trusted-verifier-invitation-list"
  );

  let currentInvitationUrl = "";

  const roleLabel = (role) =>
    String(role || "")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) =>
        character.toUpperCase()
      );

  const clearElement = (element) => {
    while (element.firstChild) {
      element.removeChild(element.firstChild);
    }
  };

  const addEmptyMessage = (element, message) => {
    const paragraph = document.createElement("p");
    paragraph.className = "body-copy";
    paragraph.textContent = message;
    element.appendChild(paragraph);
  };

  const addRecord = (
    element,
    title,
    details,
    actions = []
  ) => {
    const card = document.createElement("article");
    card.className = "mentor-card";

    const heading = document.createElement("h3");
    heading.textContent = title;

    const paragraph = document.createElement("p");
    paragraph.textContent = details;

    card.append(heading, paragraph);

    const actionList = Array.isArray(actions)
      ? actions
      : [actions];

    const visibleActions = actionList.filter(Boolean);

    if (visibleActions.length > 0) {
      const buttonRow = document.createElement("div");
      buttonRow.className = "button-row";

      visibleActions.forEach((action) => {
        const button = document.createElement("button");

        button.className =
          `btn ${action.className || "btn-secondary"}`;
        button.type = "button";
        button.textContent = action.label;

        button.addEventListener("click", () => {
          action.onClick(button);
        });

        buttonRow.appendChild(button);
      });

      card.appendChild(buttonRow);
    }

    element.appendChild(card);
  };

  const showInvitationLink = (payload) => {
    currentInvitationUrl = new URL(
      payload.accept_path,
      window.location.origin
    ).toString();

    inviteLink.textContent = currentInvitationUrl;
    successPanel.hidden = false;
    copyFeedback.textContent = "";
  };

  const runLifecycleAction = async (
    button,
    path,
    confirmation
  ) => {
    if (!window.confirm(confirmation)) {
      return;
    }

    button.disabled = true;
    errorText.textContent = "";

    try {
      const response = await fetch(path, {
        method: "DELETE",
        credentials: "same-origin",
      });

      const payload = await response.json();

      if (response.status === 401) {
        window.location.assign("/login");
        return;
      }

      if (!response.ok) {
        throw new Error(
          payload.detail || "The change could not be completed."
        );
      }

      currentInvitationUrl = "";
      successPanel.hidden = true;
      copyFeedback.textContent = "";

      await loadVerifiers();
    } catch (error) {
      errorText.textContent =
        error.message || "The change could not be completed.";
    } finally {
      button.disabled = false;
    }
  };

  const runReissueAction = async (
    button,
    invitation
  ) => {
    const confirmed = window.confirm(
      "Create a new private invitation link for " +
      `${invitation.email}? The previous link will stop working.`
    );

    if (!confirmed) {
      return;
    }

    button.disabled = true;
    errorText.textContent = "";
    copyFeedback.textContent = "";

    try {
      const response = await fetch(
        (
          "/trusted-verifiers/invitations/" +
          invitation.id +
          "/reissue"
        ),
        {
          method: "POST",
          credentials: "same-origin",
        }
      );

      const payload = await response.json();

      if (response.status === 401) {
        window.location.assign("/login");
        return;
      }

      if (!response.ok) {
        throw new Error(
          payload.detail ||
          "The invitation link could not be reissued."
        );
      }

      showInvitationLink(payload);

      copyFeedback.textContent =
        "New link created. The previous link no longer works.";

      await loadVerifiers();
    } catch (error) {
      errorText.textContent =
        error.message ||
        "The invitation link could not be reissued.";
    } finally {
      button.disabled = false;
    }
  };

  const loadVerifiers = async () => {
    const response = await fetch(
      "/trusted-verifiers/invitations",
      {
        credentials: "same-origin",
      }
    );

    if (response.status === 401) {
      window.location.assign("/login");
      return;
    }

    const payload = await response.json();

    if (!response.ok) {
      throw new Error(
        payload.detail || "Could not load trusted verifiers."
      );
    }

    clearElement(connectionList);
    clearElement(invitationList);

    const activeConnections = payload.connections.filter(
      (connection) => connection.status === "accepted"
    );

    if (activeConnections.length === 0) {
      addEmptyMessage(
        connectionList,
        "No trusted adults are connected yet."
      );
    } else {
      activeConnections.forEach((connection) => {
        const verifierName =
          connection.verifier.display_name;

        addRecord(
          connectionList,
          verifierName,
          `${roleLabel(connection.role)} · Connected`,
          {
            label: "Disconnect",
            className: "btn-red",
            onClick: (button) =>
              runLifecycleAction(
                button,
                (
                  "/trusted-verifiers/connections/" +
                  connection.id
                ),
                (
                  `Disconnect ${verifierName} from ` +
                  "this Woodchuck?"
                )
              ),
          }
        );
      });
    }

    const pendingInvitations = payload.invitations.filter(
      (invitation) => invitation.status === "pending"
    );

    if (pendingInvitations.length === 0) {
      addEmptyMessage(
        invitationList,
        "No open invitations."
      );
    } else {
      pendingInvitations.forEach((invitation) => {
        addRecord(
          invitationList,
          invitation.email,
          `${roleLabel(invitation.role)} · Pending`,
          [
            {
              label: "Reissue Link",
              className: "btn-secondary",
              onClick: (button) =>
                runReissueAction(
                  button,
                  invitation
                ),
            },
            {
              label: "Cancel Invitation",
              className: "btn-red",
              onClick: (button) =>
                runLifecycleAction(
                  button,
                  (
                    "/trusted-verifiers/invitations/" +
                    invitation.id
                  ),
                  (
                    `Cancel the invitation for ` +
                    `${invitation.email}?`
                  )
                ),
            },
          ]
        );
      });
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    errorText.textContent = "";
    copyFeedback.textContent = "";
    successPanel.hidden = true;

    try {
      const response = await fetch(
        "/trusted-verifiers/invitations",
        {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
        }
      );

      const payload = await response.json();

      if (!response.ok) {
        throw new Error(
          payload.detail || "Could not create the invitation."
        );
      }

      showInvitationLink(payload);
      form.reset();

      await loadVerifiers();
    } catch (error) {
      errorText.textContent =
        error.message || "Could not create the invitation.";
    }
  });

  copyButton.addEventListener("click", async () => {
    if (!currentInvitationUrl) {
      return;
    }

    try {
      await navigator.clipboard.writeText(
        currentInvitationUrl
      );

      copyFeedback.textContent =
        "Invitation link copied.";
    } catch {
      copyFeedback.textContent =
        "Select and copy the invitation link above.";
    }
  });

  loadVerifiers().catch((error) => {
    connectionList.textContent =
      error.message || "Could not load trusted verifiers.";

    invitationList.textContent = "";
  });
})();
