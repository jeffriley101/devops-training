(function (root) {
  "use strict";

  let activeReaction = null;

  function dismissCharacterReaction() {
    if (!activeReaction) return false;
    const reaction = activeReaction;
    activeReaction = null;
    reaction.remove();
    return true;
  }

  function playCharacterWhistle() {
    try {
      const audio = root.WoodshedAudio;
      if (!audio || typeof audio.play !== "function") return false;
      return audio.play("characterWhistle");
    } catch (_error) {
      return false;
    }
  }

  function authenticatedPlayerName() {
    try {
      const data = root.document && root.document.getElementById("authenticated-player-name");
      if (!data) return "";
      const value = JSON.parse(data.textContent);
      return typeof value === "string" ? value.trim() : "";
    } catch (_error) {
      return "";
    }
  }

  function personalizedMessage(message) {
    const base = String(message || "").trim();
    const playerName = authenticatedPlayerName();
    if (!playerName) return base;
    return `${base.replace(/!+$/, "")}, ${playerName}!`;
  }

  function showCharacterReaction(options) {
    const settings = options || {};
    if (!root.document || !root.document.body || !settings.imageUrl || !settings.message) {
      return false;
    }

    try {
      dismissCharacterReaction();
      const reaction = root.document.createElement("aside");
      reaction.className = "ww-character-reaction";
      reaction.setAttribute("role", "dialog");
      reaction.setAttribute("aria-modal", "true");
      reaction.setAttribute("aria-live", "polite");
      reaction.setAttribute("aria-label", settings.characterName || "Character reaction");

      const image = root.document.createElement("img");
      image.src = settings.imageUrl;
      image.alt = "";
      image.setAttribute("aria-hidden", "true");

      const speech = root.document.createElement("p");
      speech.className = "ww-character-reaction-speech";

      const speaker = root.document.createElement("strong");
      speaker.className = "ww-character-reaction-speaker";
      speaker.textContent = settings.characterName || "Character";

      const quote = root.document.createElement("span");
      quote.className = "ww-character-reaction-quote";
      quote.textContent = `: “${personalizedMessage(settings.message)}”`;
      speech.append(speaker, quote);

      const dismiss = root.document.createElement("button");
      dismiss.className = "ww-character-reaction-dismiss";
      dismiss.type = "button";
      dismiss.setAttribute("aria-label", "Dismiss character reaction");
      dismiss.textContent = "×";

      reaction.append(speech, image, dismiss);
      reaction.addEventListener("click", dismissCharacterReaction);
      dismiss.addEventListener("click", function (event) {
        event.stopPropagation();
        dismissCharacterReaction();
      });
      reaction.addEventListener("keydown", function (event) {
        if (event.key === "Escape") dismissCharacterReaction();
      });
      root.document.body.appendChild(reaction);
      activeReaction = reaction;
      root.requestAnimationFrame(function () { reaction.classList.add("is-visible"); });
      playCharacterWhistle();
      return true;
    } catch (_error) {
      return false;
    }
  }

  root.WoodshedCharacterReaction = Object.freeze({
    show: showCharacterReaction,
    dismiss: dismissCharacterReaction,
  });
}(typeof window !== "undefined" ? window : globalThis));
