(() => {
  /**
   * ProgressStatus — short label + animated ellipsis.
   * prefers-reduced-motion → static "..."
   * On finish: setDone / setError (no dots). Disables a control while running.
   */
  class ProgressStatus {
    /**
     * @param {HTMLElement|null} el target status element
     * @param {{ control?: HTMLElement|null }} [opts]
     */
    constructor(el, opts) {
      this.el = el;
      this.control = (opts && opts.control) || null;
      this._label = "";
      this._running = false;
      if (this.el) {
        this.el.setAttribute("aria-live", "polite");
        this.el.classList.add("progress-status");
      }
    }

    start(label) {
      this._running = true;
      this._label = label || "Working";
      if (this.control) this.control.disabled = true;
      this._renderRunning();
    }

    update(label) {
      if (!this._running) return;
      this._label = label || this._label;
      this._renderRunning();
    }

    setDone(message) {
      this._running = false;
      if (this.control) this.control.disabled = false;
      if (!this.el) return;
      this.el.hidden = false;
      this.el.classList.remove("is-running", "is-error");
      this.el.classList.add("is-done");
      this.el.textContent = message || "Done.";
    }

    setError(message) {
      this._running = false;
      if (this.control) this.control.disabled = false;
      if (!this.el) return;
      this.el.hidden = false;
      this.el.classList.remove("is-running", "is-done");
      this.el.classList.add("is-error");
      this.el.textContent = message || "Failed.";
    }

    clear() {
      this._running = false;
      if (this.control) this.control.disabled = false;
      if (!this.el) return;
      this.el.classList.remove("is-running", "is-done", "is-error");
      this.el.textContent = "";
      this.el.hidden = true;
    }

    get running() {
      return this._running;
    }

    _renderRunning() {
      if (!this.el) return;
      this.el.hidden = false;
      this.el.classList.remove("is-done", "is-error");
      this.el.classList.add("is-running");
      const reduce =
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduce) {
        this.el.textContent = `${this._label}...`;
        return;
      }
      this.el.innerHTML = `${escapeHtml(this._label)}<span class="progress-ellipsis" aria-hidden="true"></span>`;
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  window.ProgressStatus = ProgressStatus;
})();
