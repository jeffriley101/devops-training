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
      .replace(/\b\w/g, (character) => character.toUpperCase());

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

  const addRecord = (element, title, details) => {
    const card = document.createElement("article");
    card.className = "mentor-card";

    const heading = document.createElement("h3");
    heading.textContent = title;

    const paragraph = document.createElement("p");
    paragraph.textContent = details;

    card.append(heading, paragraph);
    element.appendChild(card);
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

    if (payload.connections.length === 0) {
      addEmptyMessage(
        connectionList,
        "No trusted adults are connected yet."
      );
    } else {
      payload.connections.forEach((connection) => {
        addRecord(
          connectionList,
          connection.verifier.display_name,
          `${roleLabel(connection.role)} · ${connection.status}`
        );
      });
    }

    const visibleInvitations = payload.invitations.filter(
      (invitation) => invitation.status !== "accepted"
    );

    if (visibleInvitations.length === 0) {
      addEmptyMessage(
        invitationList,
        "No open invitations."
      );
    } else {
      visibleInvitations.forEach((invitation) => {
        addRecord(
          invitationList,
          invitation.email,
          `${roleLabel(invitation.role)} · ${invitation.status}`
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

      currentInvitationUrl = new URL(
        payload.accept_path,
        window.location.origin
      ).toString();

      inviteLink.textContent = currentInvitationUrl;
      successPanel.hidden = false;
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
      copyFeedback.textContent = "Invitation link copied.";
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
