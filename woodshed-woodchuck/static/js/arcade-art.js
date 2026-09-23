(function (root) {
  "use strict";
  // Artwork replaces these slots with reviewed same-origin assets; no gameplay depends on them.
  const keys = ["plunge-burrow", "blue", "radio-tuner", "wheel-of-woodchuck", "scale-keyboard",
    "thirds", "dressed-to-the-nines", "interval-basic-training", "history-mystery",
    "note-names", "instrument-fingerings", "rhythm-hear-pick", "key-signatures", "transposition"];
  // Unassigned slots intentionally preserve the cabinet DOM, including legacy crops.
  // Add only reviewed per-game assets here; the keeper sheet is not a tile.
  const assets = Object.fromEntries(keys.map(key => [key, {
    tile: null,
    static: null,
    animated: null, entrance: null, transition: null, win: null,
  }]));
  // Lossless PNGs derived from approved originals with nearest-neighbor resizing.
  const reviewedTiles = {
    "plunge-burrow": "/static/img/arcade/plunge-burrow/tile.v1.png",
    "blue": "/static/img/arcade/blue/tile.v1.png",
    "radio-tuner": "/static/img/arcade/radio-tuner/tile.v1.png",
    "scale-keyboard": "/static/img/arcade/scale-keyboard/tile.v1.png",
    "thirds": "/static/img/arcade/thirds/tile.v1.png",
    "wheel-of-woodchuck": "/static/img/arcade/wheel-of-woodchuck/tile.v1.png",
    "note-names": "/static/img/arcade/note-names/tile.v1.png",
    "instrument-fingerings": "/static/img/arcade/instrument-fingerings/tile.v1.png",
    "rhythm-hear-pick": "/static/img/arcade/rhythm-hear-pick/tile.v1.png",
    "key-signatures": "/static/img/arcade/key-signatures/tile.v1.png",
    "transposition": "/static/img/arcade/transposition/tile.v1.png",
  };
  Object.entries(reviewedTiles).forEach(([key, url]) => {
    assets[key].tile = url;
    assets[key].static = url.replace("/tile.v1.png", "/static.v1.png");
  });
  function wire(element, asset) {
    if (!asset || !(asset.static || asset.tile)) return;
    const image = document.createElement("img");
    image.alt = ""; image.loading = "lazy"; image.decoding = "async";
    image.classList.add("arcade-art-image");
    const reduced = root.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = false;
    let failed = false;
    let staticLoaded = false;
    let unavailable = false;
    const staticSource = asset.static || asset.tile;
    function activate(active) {
      // Keep a layout box while loading so native lazy loading can observe it.
      image.hidden = false;
      element.classList.toggle("arcade-art-active", active);
    }
    activate(false);
    function update() {
      if (unavailable) return;
      const src = staticLoaded && !failed && visible && !document.hidden && !reduced.matches && asset.animated
        ? asset.animated : staticSource;
      if (image.getAttribute("src") !== src) {
        activate(false);
        image.src = src;
      }
    }
    image.addEventListener("load", function () {
      if (unavailable || !image.naturalWidth) return;
      if (image.getAttribute("src") === staticSource) staticLoaded = true;
      activate(true);
      update();
    });
    image.addEventListener("error", function () {
      activate(false);
      if (image.getAttribute("src") === asset.animated) { failed = true; update(); }
      else { unavailable = true; image.hidden = true; } // Restore the legacy visual.
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
