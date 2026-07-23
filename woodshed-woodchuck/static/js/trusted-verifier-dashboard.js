(() => {
  const studentList = document.querySelector(
    "#verifier-student-list"
  );

  const practiceChartList = document.querySelector(
    "#verifier-practice-chart-list"
  );

  if (!studentList || !practiceChartList) {
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

  const feedbackText = document.querySelector(
    "#verifier-practice-chart-feedback"
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

  const addText = (
    parent,
    tagName,
    text,
    className = ""
  ) => {
    const element = document.createElement(tagName);
    element.textContent = text;

    if (className) {
      element.className = className;
    }

    parent.appendChild(element);
    return element;
  };

  const redirectToLogin = () => {
    window.location.assign(
      "/trusted-verifiers/login"
    );
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

  const respondToPracticeChart = async (
    item,
    decision,
    responseNote,
    buttons
  ) => {
    buttons.forEach((button) => {
      button.disabled = true;
    });

    errorText.textContent = "";
    feedbackText.textContent = "";

    try {
      const response = await fetch(
        (
          "/trusted-verifiers/practice-charts/" +
          item.verification_id +
          "/respond"
        ),
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            decision,
            response_note: responseNote,
          }),
        }
      );

      const payload = await response.json();

      if (response.status === 401) {
        redirectToLogin();
        return;
      }

      if (!response.ok) {
        throw new Error(
          payload.detail ||
          "The P-Chart response could not be saved."
        );
      }

      feedbackText.textContent =
        decision === "approved"
          ? "P-Chart approved."
          : "P-Chart rejected. The student can review your note.";

      await loadPracticeCharts();
    } catch (error) {
      errorText.textContent =
        error.message ||
        "The P-Chart response could not be saved.";

      buttons.forEach((button) => {
        button.disabled = false;
      });
    }
  };

  const renderPracticeChart = (item) => {
    const student = item.student;
    const chart = item.chart;
    const card = document.createElement("article");

    card.className = "mentor-card";

    addText(
      card,
      "h3",
      `${student.display_name}'s P-Chart`
    );

    addText(
      card,
      "p",
      (
        `${chart.practice_date} · ` +
        `${chart.minutes} minutes · ` +
        chart.instrument
      )
    );

    if (
      Array.isArray(chart.practice_details) &&
      chart.practice_details.length > 0
    ) {
      addText(
        card,
        "p",
        `Worked on: ${chart.practice_details.join(", ")}`
      );
    }

    if (chart.note) {
      addText(
        card,
        "p",
        `Student note: ${chart.note}`
      );
    }

    const noteLabel = document.createElement("label");
    const noteId =
      `verification-response-note-${item.verification_id}`;

    noteLabel.htmlFor = noteId;
    noteLabel.textContent = "Response note (optional)";

    const noteInput = document.createElement("textarea");

    noteInput.id = noteId;
    noteInput.rows = 3;
    noteInput.maxLength = 300;
    noteInput.placeholder =
      "Add encouragement or explain what needs correction.";

    const buttonRow = document.createElement("div");
    buttonRow.className = "button-row";

    const approveButton = document.createElement("button");
    approveButton.className = "btn btn-green";
    approveButton.type = "button";
    approveButton.textContent = "Approve";

    const rejectButton = document.createElement("button");
    rejectButton.className = "btn btn-red";
    rejectButton.type = "button";
    rejectButton.textContent = "Reject";

    const buttons = [
      approveButton,
      rejectButton,
    ];

    approveButton.addEventListener("click", () => {
      respondToPracticeChart(
        item,
        "approved",
        noteInput.value.trim(),
        buttons
      );
    });

    rejectButton.addEventListener("click", () => {
      respondToPracticeChart(
        item,
        "rejected",
        noteInput.value.trim(),
        buttons
      );
    });

    buttonRow.append(
      approveButton,
      rejectButton
    );

    card.append(
      noteLabel,
      noteInput,
      buttonRow
    );

    practiceChartList.appendChild(card);
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
      redirectToLogin();
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
        "No musicians are connected to this account yet.",
        "body-copy"
      );

      return;
    }

    payload.student_connections.forEach(
      renderStudent
    );
  };

  const loadPracticeCharts = async () => {
    const response = await fetch(
      "/trusted-verifiers/practice-charts",
      {
        credentials: "same-origin",
      }
    );

    const payload = await response.json();

    if (response.status === 401) {
      redirectToLogin();
      return;
    }

    if (!response.ok) {
      throw new Error(
        payload.detail ||
        "Could not load pending P-Charts."
      );
    }

    clearElement(practiceChartList);

    if (payload.pending_charts.length === 0) {
      addText(
        practiceChartList,
        "p",
        "No P-Charts are waiting for review.",
        "body-copy"
      );

      return;
    }

    payload.pending_charts.forEach(
      renderPracticeChart
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

      redirectToLogin();
    } catch (error) {
      logoutButton.disabled = false;
      errorText.textContent =
        error.message || "Could not sign out.";
    }
  });

  Promise.all([
    loadDashboard(),
    loadPracticeCharts(),
  ]).catch((error) => {
    errorText.textContent =
      error.message || "Could not load the dashboard.";
  });
})();
