(function (root) {
  "use strict";

  const SUCCESS_CONTROL_IDS = Object.freeze([
    "instrument-object",
    "xp-level-control",
    "shed-decorate-button",
    "mum-open-button",
    "shed-team-button",
    "tuner-open-button",
    "sound-effects-button",
  ]);

  function restartAnimation(element, className) {
    if (!element) return;
    element.classList.remove(className);
    void element.offsetWidth;
    element.classList.add(className);
  }

  function clearReactionClasses(element) {
    if (!element) return;
    element.classList.remove("is-tap-wiggle", "is-achievement-hop");
  }

  function initWoodchuckMotion() {
    const layer = root.document && root.document.querySelector(".woodshed-character-layer");
    const art = layer && layer.querySelector(".woodshed-character-art");
    if (!layer || !art) return false;

    layer.classList.add("ww-motion-ready");
    const presentationOnly = layer.hasAttribute("data-presentation-only");
    if (!presentationOnly) {
      layer.setAttribute("role", "button");
      layer.setAttribute("tabindex", "0");
    }
    layer.setAttribute("aria-label", "Woodchuck");

    art.addEventListener("animationend", function (event) {
      if (event.animationName === "ww-woodchuck-tap-wiggle") {
        art.classList.remove("is-tap-wiggle");
      }
      if (event.animationName === "ww-woodchuck-achievement-hop") {
        art.classList.remove("is-achievement-hop");
      }
    });

    let reducedReactionTimer;
    function wiggle() {
      clearReactionClasses(art);
      restartAnimation(art, "is-tap-wiggle");
      clearTimeout(reducedReactionTimer);
      reducedReactionTimer = setTimeout(() => art.classList.remove("is-tap-wiggle"), 450);
    }

    function hop() {
      clearReactionClasses(art);
      restartAnimation(art, "is-achievement-hop");
    }

    if (!presentationOnly) layer.addEventListener("click", wiggle);
    if (!presentationOnly) layer.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      wiggle();
    });

    const target = layer.querySelector(".character-reaction-target");
    const scene = layer.closest?.(".artwork-scene");
    if (target && scene) {
      let alphaSource = "", pixels = null, width = 0, height = 0;
      function hitCharacter(x, y) {
        if (!art.hasAttribute("data-appearance-ready") || !art.naturalWidth || scene.classList.contains("is-decorating")) return false;
        if (alphaSource !== art.src) {
          // Same-origin appearance assets (including generated data URLs) only.
          // If an asset cannot be sampled, keep room navigation operational.
          try {
            const canvas = document.createElement("canvas");
            const ratio = Math.min(1, 512 / Math.max(art.naturalWidth, art.naturalHeight));
            width = canvas.width = Math.max(1, Math.round(art.naturalWidth * ratio));
            height = canvas.height = Math.max(1, Math.round(art.naturalHeight * ratio));
            const context = canvas.getContext("2d", {willReadFrequently: true});
            context.drawImage(art, 0, 0, width, height);
            pixels = context.getImageData(0, 0, width, height).data;
          } catch (_) { pixels = null; }
          alphaSource = art.src;
        }
        if (!pixels) return false;
        const rect = art.getBoundingClientRect();
        const scale = Math.min(rect.width / width, rect.height / height);
        const left = rect.left + (rect.width - width * scale) / 2;
        const top = rect.top + (rect.height - height * scale) / 2;
        const px = Math.floor((x - left) / scale), py = Math.floor((y - top) / scale);
        return px >= 0 && px < width && py >= 0 && py < height && pixels[(py * width + px) * 4 + 3] > 24;
      }
      // The layer itself has pointer-events:none. Only an opaque character
      // pixel consumes a real pointer click; transparent space reaches its cell.
      scene.addEventListener("click", event => {
        if (!event.detail || !hitCharacter(event.clientX, event.clientY)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        target.focus({preventScroll: true});
        wiggle();
      }, true);
      target.addEventListener("click", event => {
        event.preventDefault(); event.stopPropagation(); wiggle();
      });
    }

    SUCCESS_CONTROL_IDS.forEach(function (controlId) {
      const control = root.document.getElementById(controlId);
      if (control) control.addEventListener("click", hop);
    });

    root.addEventListener("woodshed:practice-sway", function (event) {
      const active = Boolean(event.detail && event.detail.active);
      if (active) {
        art.classList.add("is-practice-sway");
      } else {
        art.classList.remove("is-practice-sway");
      }
    });

    root.addEventListener("woodshed:celebrate", hop);

    return true;
  }

  if (root.document && root.document.readyState === "loading") {
    root.document.addEventListener("DOMContentLoaded", initWoodchuckMotion, { once: true });
  } else {
    initWoodchuckMotion();
  }
}(typeof window !== "undefined" ? window : globalThis));
