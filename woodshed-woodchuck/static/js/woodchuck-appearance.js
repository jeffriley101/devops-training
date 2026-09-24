(function () {
  'use strict';
  const cache = new Map();
  // Each classification reads the ORIGINAL RGB triplet, before either replacement.
  function recolor(pixels, hoodie, hat) {
    const output = new Uint8ClampedArray(pixels);
    for (let i = 0; i < pixels.length; i += 4) {
      const r = pixels[i] / 255, g = pixels[i + 1] / 255, b = pixels[i + 2] / 255;
      const max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
      if (delta < .012 || delta / max < .07 || !pixels[i + 3]) continue;
      const hue = ((max === r ? (g - b) / delta : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4) * 60 + 360) % 360;
      const family = hue >= 180 && hue <= 265 ? 'blue' : hue >= 55 && hue < 180 && g >= r * .97 ? 'green' : null;
      const choice = family === 'blue' ? hoodie : family === 'green' ? hat : null;
      if (!choice || choice.key === family) continue;
      let light = (max + min) / 2;
      light = light * (choice.lightness_scale ?? 1);
      light += (1 - light) * (choice.lightness_lift ?? 0);
      const sat = Math.max(choice.saturation_floor ?? .65, delta / (1 - Math.abs(2 * ((max + min) / 2) - 1)));
      const c = (1 - Math.abs(2 * light - 1)) * sat, h = choice.hue / 60;
      const x = c * (1 - Math.abs(h % 2 - 1)), m = light - c / 2;
      const rgb = h < 1 ? [c,x,0] : h < 2 ? [x,c,0] : h < 3 ? [0,c,x] : h < 4 ? [0,x,c] : h < 5 ? [x,0,c] : [c,0,x];
      for (let channel = 0; channel < 3; channel++) output[i + channel] = Math.round((rgb[channel] + m) * 255);
    }
    return output;
  }
  async function imageFor(payload) {
    const source = new URL(payload.source, location.href);
    if (source.origin !== location.origin || !source.pathname.startsWith('/static/')) throw new Error('Untrusted character image');
    const {hoodie, hat} = payload.effective;
    if (hoodie === 'blue' && hat === 'green') return source.href;
    const key = JSON.stringify([source.href, hoodie, hat]);
    if (!cache.has(key)) {
      if (cache.size >= 24) cache.delete(cache.keys().next().value);
      cache.set(key, new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => {
          try {
            const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
            const context = canvas.getContext('2d', {willReadFrequently: true});
            context.drawImage(image, 0, 0);
            const data = context.getImageData(0, 0, canvas.width, canvas.height);
            data.data.set(recolor(data.data, payload.palette.find(c => c.key === hoodie), payload.palette.find(c => c.key === hat)));
            context.putImageData(data, 0, 0); resolve(canvas.toDataURL('image/png'));
          } catch (error) { cache.delete(key); reject(error); }
        };
        image.onerror = () => { cache.delete(key); reject(new Error('Character image unavailable')); };
        image.src = source.href;
      }));
    }
    return cache.get(key);
  }
  window.WWColorizer = Object.freeze({recolor, imageFor});
  const data = document.getElementById('woodchuck-appearance-data');
  if (!data) return;
  let payload = JSON.parse(data.textContent), renderGeneration = 0;
  const dialog = document.getElementById('your-woodchuck'), form = document.getElementById('woodchuck-editor-form');
  const status = document.getElementById('woodchuck-editor-status');
  const current = () => !window.WWSessionBoundary || window.WWSessionBoundary.isCurrent();
  async function render() {
    const generation = ++renderGeneration;
    let src = payload.source;
    const images = Array.from(document.querySelectorAll('[data-student-woodchuck]'));
    images.forEach(image => image.removeAttribute('data-appearance-ready'));
    try {
      src = await imageFor(payload);
      // Decode the final (possibly generated) PNG before replacing/revealing it.
      const decoded = new Image(); decoded.src = src; await decoded.decode();
      if (generation !== renderGeneration || !current()) return;
      await Promise.all(images.map(async image => {
        image.src = src; image.alt = `Your Woodchuck playing ${payload.instrument}`;
        await image.decode();
        if (generation === renderGeneration && current()) image.setAttribute('data-appearance-ready', 'true');
      }));
    } catch (error) {
      if (generation === renderGeneration && current()) window.WWRecovery?.failure(
        new Error('Your Woodchuck image could not load.'), render);
      return;
    }
    const trigger = document.getElementById('instrument-object');
    if (trigger) {
      if (!trigger.dataset.sceneCell) window.WWInstruments?.renderInstrument(trigger, payload.instrument);
      trigger.setAttribute('aria-label', `Your Woodchuck. Instrument: ${payload.instrument}`);
      trigger.setAttribute('aria-controls', 'your-woodchuck');
    }
  }
  void render();
  // SHOP shares the rendered appearance only; customization belongs to SHED L2.
  if (!dialog || !form) return;
  function fill() {
    const instrument = form.elements.instrument;
    instrument.querySelector('[data-keep-instrument]')?.remove();
    const legacy = !Array.from(instrument.options).some(option => option.value === payload.instrument);
    const note = document.getElementById('woodchuck-legacy-instrument');
    note.hidden = !legacy;
    if (legacy) {
      note.textContent = `Current instrument: ${payload.instrument}. Keep it or choose a current instrument below.`;
      const keep = document.createElement('option'); keep.value = '';
      keep.textContent = 'Keep current instrument'; keep.dataset.keepInstrument = 'true';
      instrument.prepend(keep);
    }
    instrument.value = legacy ? '' : payload.instrument;
    for (const part of ['hoodie', 'hat']) {
      form.elements[part].replaceChildren(...payload.palette.map(color => {
        const option = document.createElement('option'); option.value = color.key;
        option.textContent = color.label + (color.premium ? ' · Premium' : '');
        option.disabled = color.premium && !payload.premium && payload.saved[part] !== color.key;
        return option;
      }));
      form.elements[part].value = payload.saved[part];
    }
  }
  function open() { if (!current() || dialog.open) return; fill(); status.textContent = ''; dialog.showModal(); }
  document.getElementById('instrument-object')?.addEventListener('click', open);
  dialog.querySelector('[data-surface-close]').addEventListener('click', () => dialog.close());
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (!current() || dialog.dataset.busy === 'true') return;
    const submitted = Object.fromEntries(new FormData(form)), button = form.querySelector('[type="submit"]');
    if (!submitted.instrument) submitted.instrument = payload.instrument;
    const request = window.WWState.accountRequest();
    const recovery = window.WWRecovery?.begin();
    button.disabled = true; dialog.dataset.busy = 'true'; status.textContent = 'Saving…';
    try {
      const response = await fetch('/account/appearance', {method: 'PATCH', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(submitted)});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(new Error(result.detail || 'Unable to save'), {status: response.status});
      const state = window.WWState.stateForResponse(request); if (!state) return;
      // The PATCH already returned the committed revision. Do not depend on a
      // second request, or overwrite a newer reward/purchase response.
      state.profile.instrument = result.instrument; state.appearance = result.saved;
      window.WWState.applyEconomy(state, result);
      window.WWState.saveState(state, {sync: false});
      payload = result; fill(); await render(); window.WWSurfaces?.markSaved(dialog);
      status.textContent = 'Your Woodchuck is saved.';
      window.WWRecovery?.clear(recovery);
    } catch (error) {
      status.textContent = error.message || 'Unable to save. Please retry.';
      if (current()) window.WWRecovery?.failure(error, () => form.requestSubmit(), recovery);
    } finally { button.disabled = false; dialog.dataset.busy = 'false'; }
  });
  // The SHED control and keyboard use the same editor.
  window.WWWoodchuck = Object.freeze({open});
})();
