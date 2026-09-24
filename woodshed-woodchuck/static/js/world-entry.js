/* Presentation only: links and server authorization remain authoritative. */
(function () {
  "use strict";
  const splashURL = "/static/img/arcade/arcade-entry-splash.png";
  const arrivalKey = "woodshed:world-entry-arrival:v1";
  const account = document.body.dataset.worldEntryAccount;
  const seenKey = `woodshed:arcade-entry:daily:v2:${account}`;
  const authenticated = document.body.dataset.authenticated === "true";
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const read = key => { try { return window.sessionStorage.getItem(key); } catch (_) { return null; } };
  const write = (key, value) => { try { window.sessionStorage.setItem(key, value); } catch (_) {} };
  const remove = key => { try { window.sessionStorage.removeItem(key); } catch (_) {} };
  const readDay = () => { try { return window.localStorage.getItem(seenKey); } catch (_) { return null; } };
  const writeDay = day => { try { window.localStorage.setItem(seenKey, day); } catch (_) {} };
  const localDay = () => {
    const date = new Date();
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  };
  let active = null;
  let seenHere = null;
  let retired = false;
  const current = () => !retired && (!window.WWSessionBoundary || window.WWSessionBoundary.isCurrent());

  // Discard the superseded per-login display state; daily state survives logout.
  remove("woodshed:arcade-entry:v1");
  let finishArrival;
  const arrivalReady = new Promise(resolve => { finishArrival = resolve; });
  window.WWWorldEntry = Object.freeze({arrivalReady});
  let arriving = false;
  // The destination fades in only after our navigation, never on ordinary visits.
  try {
    const arrival = JSON.parse(read(arrivalKey));
    remove(arrivalKey);
    if (authenticated && arrival && arrival.account === account &&
        arrival.path === window.location.pathname && arrival.expires > Date.now()) {
      arriving = true;
      document.body.classList.add("world-entry-arrival");
      window.setTimeout(() => {
        document.body.classList.remove("world-entry-arrival");
        finishArrival();
      }, reduced() ? 0 : 220);
    }
  } catch (_) { remove(arrivalKey); }
  if (!arriving) finishArrival();

  if (authenticated && document.querySelector('[data-world-entry="arcade"]')) {
    const preload = new Image();
    preload.src = splashURL;
  }

  function reset() {
    if (!active) return;
    active.timers.forEach(id => window.clearTimeout(id));
    active.animations.forEach(animation => animation.cancel());
    active.inert.forEach(([node, value]) => { node.inert = value; });
    document.body.style.overflow = active.overflow;
    document.body.classList.remove("world-entry-flight");
    if (typeof active.overlay.close === "function") active.overlay.close();
    active.overlay.remove();
    active.link.focus({preventScroll: true});
    active = null;
  }
  // Back/forward cache and cancelled navigation must never leave an inert page.
  window.addEventListener("pagehide", reset);
  window.addEventListener("pageshow", reset);
  window.addEventListener("ww:session-changed", () => { retired = true; reset(); });

  function start(link, kind, day) {
    // Warm only public assets while the approved flight plays. Fetching the
    // authenticated destination would duplicate GET side effects and must not
    // cache private HTML. The actual document remains a normal navigation.
    if (kind === "woodshed") {
      ["/static/img/shed-cabin-new.png"].forEach(src => { const image = new Image(); image.src = src; });
      const warm = document.createElement("link"); warm.rel = "preload"; warm.as = "script";
      warm.href = "/static/js/tuner.js?v=2"; document.head?.appendChild(warm);
    }
    const overlay = document.createElement("dialog");
    overlay.className = `world-entry-overlay world-entry-${kind}`;
    overlay.setAttribute("aria-label", kind === "arcade"
      ? "Entering the Arcade. Click, tap, or press Enter or Space to continue."
      : "Flying into your Woodshed");
    overlay.tabIndex = 0;
    const stage = document.createElement("div");
    stage.className = "world-entry-stage";
    const art = document.createElement("img");
    art.alt = kind === "arcade" ? "The Viking Sax’s Arcade, full of young woodchucks playing games" : "";
    stage.append(art);
    overlay.append(stage);
    if (kind === "arcade") {
      const instruction = document.createElement("p");
      instruction.className = "world-entry-instruction";
      instruction.textContent = "Tap anywhere to enter the Arcade";
      overlay.append(instruction);
    }
    document.body.append(overlay);
    const state = {link, overlay, timers: [], animations: [], inert: [],
      overflow: document.body.style.overflow, finishing: false, navigated: false};
    active = state;
    const later = (fn, ms) => { state.timers.push(window.setTimeout(fn, ms)); };
    function navigate() {
      if (state.navigated || active !== state) return;
      if (!current()) { reset(); return; }
      state.navigated = true;
      write(arrivalKey, JSON.stringify({path: new URL(link.href).pathname,
        account, expires: Date.now() + 8000}));
      // If the browser cancels/stalls navigation, restore the original controls.
      later(() => { remove(arrivalKey); reset(); }, 4000);
      try { window.location.assign(link.href); } catch (_) { reset(); }
    }
    function finish() {
      if (state.finishing || active !== state) return;
      state.finishing = true;
      state.timers.forEach(id => window.clearTimeout(id));
      overlay.classList.add("world-entry-leaving");
      later(navigate, 140);
    }
    // Only the flight advances on a timer. Arcade waits indefinitely for input.
    if (kind === "woodshed") later(finish, reduced() ? 160 : 1080);
    art.addEventListener("error", finish, {once: true});
    overlay.addEventListener("cancel", event => {
      event.preventDefault();
      if (kind === "woodshed") finish();
    });
    overlay.addEventListener("keydown", event => {
      if (event.key === "Tab") { event.preventDefault(); overlay.focus(); }
      if (kind === "arcade" && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault(); finish();
      }
    });
    if (kind === "arcade") {
      overlay.addEventListener("click", finish);
      overlay.addEventListener("pointerup", event => { if (event.button === 0) finish(); });
    }
    try {
      overlay.showModal(); // Above the existing SHOP dialog in the browser top layer.
      overlay.focus({preventScroll: true});
      state.inert = Array.from(document.body.children)
        .filter(node => node !== overlay && node.tagName !== "SCRIPT")
        .map(node => [node, node.inert]);
      state.inert.forEach(([node]) => { node.inert = true; });
      document.body.style.overflow = "hidden";
      if (kind === "arcade") {
        seenHere = day;
        writeDay(day);
        art.src = splashURL;
        if (art.complete && !art.naturalWidth) finish();
      } else {
        const original = document.querySelector(".welcome-hero-art");
        if (!original || !original.complete || !original.naturalWidth) { finish(); return; }
        art.src = original.currentSrc || original.src;
        if (!reduced() && art.animate) fly(original, art, stage, state);
        document.body.classList.add("world-entry-flight");
      }
    } catch (_) {
      // Unsupported dialog/animation APIs and missing assets cannot block the link.
      finish();
    }
  }

  function fly(original, art, stage, state) {
    const rect = original.getBoundingClientRect();
    const frame = original.closest(".welcome-hero").getBoundingClientRect();
    const w = original.naturalWidth, h = original.naturalHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    const position = window.getComputedStyle(original).objectPosition.split(" ").map(parseFloat);
    const initial = Math.max(rect.width / w, rect.height / h);
    const x = rect.left + (rect.width - w * initial) * position[0] / 100;
    const y = rect.top + (rect.height - h * initial) * position[1] / 100;
    // Full artwork coordinates, independent of the desktop/mobile object-fit crop.
    const houseX = 0.65, houseY = 0.458;
    const cover = Math.max(vw / w, vh / h) * 1.25;
    const transform = (s, left, top) => `translate(${left}px, ${top}px) scale(${s})`;
    const finalScale = cover * 12;
    const finalX = vw / 2 - w * finalScale * houseX;
    const finalY = vh / 2 - h * finalScale * houseY;
    const right = x + w * initial, bottom = y + h * initial;
    // Expand the frame as soon as this continuous camera path covers the viewport.
    // All edges are affine in the shared progress, so this also guarantees cover
    // throughout the expansion, without a separate camera waypoint.
    const coverProgress = Math.min(1, Math.max(.01,
      x > 0 ? x / (x - finalX) : 0,
      y > 0 ? y / (y - finalY) : 0,
      right < vw ? (vw - right) / (finalX + w * finalScale - right) : 0,
      bottom < vh ? (vh - bottom) / (finalY + h * finalScale - bottom) : 0,
    ) + .005);
    art.style.width = `${w}px`;
    art.style.height = `${h}px`;
    state.animations.push(stage.animate([
      {clipPath: `inset(${Math.max(0, frame.top)}px ${Math.max(0, vw - frame.right)}px ${Math.max(0, vh - frame.bottom)}px ${Math.max(0, frame.left)}px round 16px)`},
      {clipPath: "inset(0px round 0px)", offset: coverProgress},
      {clipPath: "inset(0px round 0px)"},
    ], {duration: 1080, fill: "both", easing: "cubic-bezier(.7,0,.9,.35)"}));
    // One shared interpolation for x, y and scale: no intermediate, axis-clamped
    // camera stop. Match the crop's progress so it never exposes empty margins.
    state.animations.push(art.animate([
      {transform: transform(initial, x, y), offset: 0},
      {transform: transform(finalScale, finalX, finalY), offset: 1},
    ], {duration: 1080, fill: "both", easing: "cubic-bezier(.7,0,.9,.35)"}));
  }

  document.addEventListener("click", event => {
    const link = event.target.closest("a[data-world-entry]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey ||
        event.ctrlKey || event.shiftKey || event.altKey || link.target === "_blank" ||
        link.hasAttribute("download") || link.getAttribute("aria-disabled") === "true" ||
        !authenticated || !account || !current()) return;
    if (active) { event.preventDefault(); return; }
    const kind = link.dataset.worldEntry;
    if (kind !== "woodshed" && kind !== "arcade") return;
    // Evaluate at activation, including a SHOP page kept open across midnight.
    const day = kind === "arcade" ? localDay() : null;
    if (kind === "arcade" && (seenHere === day || readDay() === day)) return;
    event.preventDefault();
    start(link, kind, day);
  });
})();
