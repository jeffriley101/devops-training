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

      const completedButton = decision === "approved" ? buttons[0] : buttons[1];
      if (completedButton) completedButton.classList.add("is-confirmed-success");

      await refreshSnapshot();
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

  // Delegation survives replacing the snapshot after a review response.
  studentList.addEventListener("change", (event) => {
    if (event.target.id === "verifier-connection") event.target.form.requestSubmit();
  });

  const updateReviewNotice = (count) => {
    const notice = document.querySelector("#verifier-review-notice");
    if (notice) {
      notice.hidden = count === 0;
      notice.querySelector("a").textContent = count === 1
        ? "1 P-Chart needs your review" : `${count} P-Charts need your review`;
    }
    const reviews = document.querySelector("#verifier-reviews");
    if (reviews) reviews.dataset.pending = String(count > 0);
  };

  const refreshSnapshot = async () => {
    const url = "/trusted-verifiers/dashboard?connection_id=" + encodeURIComponent(studentList.dataset.connectionId);
    const response = await fetch(url, {credentials: "same-origin", cache: "no-store"});
    if (!response.ok || response.redirected) {
      clearElement(studentList);
      clearElement(practiceChartList);
      window.location.assign("/trusted-verifiers/dashboard");
      return;
    }
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const snapshot = page.querySelector("#verifier-student-list");
    if (snapshot) studentList.replaceChildren(...snapshot.childNodes);
  };

  const loadPracticeCharts = async () => {
    const connectionId = studentList.dataset.connectionId;
    if (!connectionId) {
      updateReviewNotice(0);
      clearElement(practiceChartList);
      addText(practiceChartList, "p", "No P-Charts are waiting for review.", "body-copy");
      return;
    }
    const response = await fetch(
      "/trusted-verifiers/practice-charts?connection_id=" + encodeURIComponent(connectionId),
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
      clearElement(studentList);
      clearElement(practiceChartList);
      updateReviewNotice(0);
      throw new Error(
        payload.detail ||
        "Could not load pending P-Charts."
      );
    }

    clearElement(practiceChartList);
    updateReviewNotice(payload.pending_charts.length);

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

  loadPracticeCharts().catch((error) => {
    errorText.textContent =
      error.message || "Could not load the dashboard.";
  });
})();
