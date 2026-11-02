/* Private assignment views must revalidate on browser history restoration. */
(() => {
  "use strict";
  let cleared = false;
  const clear = () => {
    document.querySelectorAll('[data-choice-private]').forEach(node => node.replaceChildren());
    document.querySelectorAll('input[type=password]').forEach(node => { node.value = ''; });
    cleared = true;
  };
  addEventListener('pagehide', clear);
  addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clear();
    else if (cleared) location.reload();
  });
})();
