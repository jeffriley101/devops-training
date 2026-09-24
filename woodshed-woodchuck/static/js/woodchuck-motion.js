(function (root) {
  "use strict";

  const SUCCESS_CONTROL_IDS = Object.freeze([
    "woodchuck-name-value",
    "instrument-object",
    "xp-level-control",
    "shed-decorate-button",
    "mum-open-button",
    "shed-team-button",
    "level-value",
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

    function wiggle() {
      if (art.classList.contains("is-achievement-hop")) return;
      clearReactionClasses(art);
      restartAnimation(art, "is-tap-wiggle");
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
