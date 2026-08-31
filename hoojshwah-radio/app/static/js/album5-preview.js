(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.Album5Preview = api;
  }
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  class PreviewState {
    constructor(tracks) {
      this.tracks = Array.isArray(tracks) ? tracks.slice() : [];
      this.queued = false;
      this.active = false;
      this.index = -1;
    }

    queue() {
      if (this.tracks.length === 0 || this.active) return false;
      this.queued = true;
      return true;
    }

    begin() {
      if (!this.queued || this.tracks.length === 0) return null;
      this.queued = false;
      this.active = true;
      this.index = 0;
      return this.current();
    }

    current() {
      return this.active ? this.tracks[this.index] || null : null;
    }

    next() {
      if (!this.active) return { action: "none", track: null };
      if (this.index + 1 < this.tracks.length) {
        this.index += 1;
        return { action: "track", track: this.current() };
      }
      this.exit();
      return { action: "return-normal", track: null };
    }

    exit() {
      const changed = this.queued || this.active;
      this.queued = false;
      this.active = false;
      this.index = -1;
      return changed;
    }
  }

  return { PreviewState };
}));
