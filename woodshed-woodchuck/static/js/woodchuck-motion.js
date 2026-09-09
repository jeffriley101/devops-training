(function (root) {
  "use strict";

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
    layer.setAttribute("role", "button");
    layer.setAttribute("tabindex", "0");
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

    layer.addEventListener("click", wiggle);
    layer.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      wiggle();
    });

    root.addEventListener("woodshed:celebrate", function () {
      clearReactionClasses(art);
      restartAnimation(art, "is-achievement-hop");
    });

    return true;
  }

  if (root.document && root.document.readyState === "loading") {
    root.document.addEventListener("DOMContentLoaded", initWoodchuckMotion, { once: true });
  } else {
    initWoodchuckMotion();
  }
}(typeof window !== "undefined" ? window : globalThis));
