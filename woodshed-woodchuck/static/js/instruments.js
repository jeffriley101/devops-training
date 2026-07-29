(function () {
  const dataEl = document.getElementById("instrument-definitions-data");
  let definitions = [];
  if (dataEl) {
    try {
      const parsed = JSON.parse(dataEl.textContent);
      if (Array.isArray(parsed)) definitions = parsed;
    } catch (_error) {
      definitions = [];
    }
  }

  function getDefinition(label) {
    if (typeof label !== "string") return null;
    const normalized = label.trim().toLocaleLowerCase();
    return definitions.find(
      (item) => item && item.label.toLocaleLowerCase() === normalized
    ) || null;
  }

  function renderInstrument(target, label) {
    if (!target) return;
    const definition = getDefinition(label);
    const fallback = definition && definition.fallback_symbol
      ? definition.fallback_symbol
      : "♪";
    target.replaceChildren();
    target.title = definition ? definition.label : "Instrument not set";
    target.setAttribute(
      "aria-label",
      definition ? definition.label : "Instrument not set"
    );

    if (
      definition &&
      definition.icon_type === "image" &&
      typeof definition.image_url === "string"
    ) {
      const image = document.createElement("img");
      image.className = "shed-instrument-image";
      image.src = definition.image_url;
      image.alt = "";
      image.addEventListener("error", function () {
        target.replaceChildren(document.createTextNode(fallback));
      }, { once: true });
      target.appendChild(image);
      return;
    }

    target.textContent = definition && definition.icon
      ? definition.icon
      : fallback;
  }

  function teamLabel(label) {
    const definition = getDefinition(label);
    return definition && typeof definition.team_label === "string"
      ? definition.team_label
      : `The ${label}`;
  }

  window.WWInstruments = {
    definitions,
    getDefinition,
    teamLabel,
    renderInstrument,
  };
})();
