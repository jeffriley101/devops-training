(function () {
  "use strict";
  function seconds(value) {
    const total = Math.max(0, Math.round(Number(value) || 0));
    const parts = [[Math.floor(total / 3600), "h"], [Math.floor(total % 3600 / 60), "m"], [total % 60, "s"]];
    return parts.filter(([amount]) => amount).map(([amount, unit]) => `${amount}${unit}`).join(" ") || "0m";
  }
  function entrySeconds(entry) {
    if (Number.isInteger(entry.durationSeconds) && entry.durationSeconds >= 0) return entry.durationSeconds;
    if ((entry.source === "pristine" || entry.pristine === true || entry.isPristine === true) &&
        Number.isInteger(entry.detectedPlayingSeconds) && entry.detectedPlayingSeconds >= 0 && entry.detectedPlayingSeconds <= 86400) {
      return entry.detectedPlayingSeconds;
    }
    return Math.max(0, Number(entry.minutes) || 0) * 60;
  }
  window.WWPracticeDuration = {seconds, minutes: value => seconds(value * 60), entrySeconds};
}());
