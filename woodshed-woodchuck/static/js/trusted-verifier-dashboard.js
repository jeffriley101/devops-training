(() => {
  const studentList = document.querySelector(
    "#verifier-student-list"
  );

  if (!studentList) {
    return;
  }

  const title = document.querySelector(
    "#verifier-dashboard-title"
  );
  const email = document.querySelector(
    "#verifier-dashboard-email"
  );
  const errorText = document.querySelector(
    "#verifier-dashboard-error"
  );
  const logoutButton = document.querySelector(
    "#trusted-verifier-logout-button"
  );

  const clearElement = (element) => {
    while (element.firstChild) {
      element.removeChild(element.firstChild);
    }
  };

  const roleLabel = (role) =>
    String(role || "")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) =>
        character.toUpperCase()
      );

  const addText = (parent, tagName, text) => {
    const element = document.createElement(tagName);
    element.textContent = text;
    parent.appendChild(element);
  };

  const renderStudent = (connection) => {
    const student = connection.student;
    const card = document.createElement("article");
    card.className = "mentor-card";

    addText(card, "h3", student.display_name);

    addText(
      card,
      "p",
      `${student.instrument} · ${student.level}`
    );

    addText(
      card,
      "p",
      `Practice goal: ${student.goal}`
    );

    addText(
      card,
      "p",
      `Your role: ${roleLabel(connection.role)}`
    );

    studentList.appendChild(card);
  };

  const loadDashboard = async () => {
    const response = await fetch(
      "/trusted-verifiers/me",
      {
        credentials: "same-origin",
      }
    );

    const payload = await response.json();

    if (
      !response.ok ||
      payload.authenticated !== true
    ) {
      window.location.assign(
        "/trusted-verifiers/login"
      );
      return;
    }

    title.textContent =
      `${payload.verifier.display_name}'s Musicians`;

    email.textContent = payload.verifier.email;

    clearElement(studentList);

    if (payload.student_connections.length === 0) {
      addText(
        studentList,
        "p",
        "No musicians are connected to this account yet."
      );
      return;
    }

    payload.student_connections.forEach(
      renderStudent
    );
  };

  logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    errorText.textContent = "";

    try {
      const response = await fetch(
        "/trusted-verifiers/logout",
        {
          method: "POST",
          credentials: "same-origin",
        }
      );

      if (!response.ok) {
        throw new Error("Could not sign out.");
      }

      window.location.assign(
        "/trusted-verifiers/login"
      );
    } catch (error) {
      logoutButton.disabled = false;
      errorText.textContent =
        error.message || "Could not sign out.";
    }
  });

  loadDashboard().catch((error) => {
    errorText.textContent =
      error.message || "Could not load the dashboard.";
  });
})();
