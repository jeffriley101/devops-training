(function (root) {
  "use strict";
  // Artwork replaces these slots with reviewed same-origin assets; no gameplay depends on them.
  const keys = ["plunge-burrow", "blue", "radio-tuner", "wheel-of-woodchuck", "scale-keyboard",
    "thirds", "dressed-to-the-nines", "interval-basic-training", "history-mystery",
    "note-names", "instrument-fingerings", "rhythm-hear-pick", "key-signatures", "transposition"];
  const assets = Object.fromEntries(keys.map(key => [key, {
    tile: "/static/img/arcade/Woodshed_Arcade_8bit_Icons_KEEPER.png",
    static: "/static/img/arcade/Woodshed_Arcade_8bit_Icons_KEEPER.png",
    animated: null, entrance: null, transition: null, win: null,
  }]));
  function wire(element, asset) {
    if (!asset) return;
    const image = element.querySelector("img") || document.createElement("img");
    image.alt = ""; image.loading = "lazy"; image.decoding = "async";
    image.classList.add("arcade-art-image");
    const reduced = root.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = false;
    let failed = false;
    function update() {
      const src = !failed && visible && !document.hidden && !reduced.matches && asset.animated
        ? asset.animated : asset.static || asset.tile;
      if (image.getAttribute("src") !== src) image.src = src;
    }
    image.addEventListener("error", function () {
      if (image.getAttribute("src") === asset.animated) { failed = true; update(); }
      else image.hidden = true; // Existing text/icon placeholder remains underneath.
    });
    element.append(image);
    if (root.IntersectionObserver) {
      new root.IntersectionObserver(entries => { visible = entries[0].isIntersecting; update(); }).observe(element);
    } // Without IntersectionObserver keep the static fallback.
    reduced.addEventListener?.("change", update);
    document.addEventListener("visibilitychange", update);
    update();
  }
  root.WoodshedArcadeArt = { assets, wire };
  document.querySelectorAll("[data-arcade-art]").forEach(element => wire(element, assets[element.dataset.arcadeArt]));
}(window));
