'use strict';
/**
 * Session timer with the 180s silence cutoff. Singleton so anything in the
 * runtime can call sessionTimer().markActivity('vad') from anywhere.
 *
 * Mirrors the same logic that lives Mac-side in omniclaw/voice/session.py
 * (added in todo session_lifecycle_storage). Keep them in sync.
 */
const SILENCE_CUTOFF_MS = 180_000;
const CHIME_MS = 200; // soft cue tone duration; actual playback is platform-side

let _instance = null;

class SessionTimer {
  constructor({ cutoffMs = SILENCE_CUTOFF_MS, onTimeout } = {}) {
    this.cutoffMs = cutoffMs;
    this.onTimeout = onTimeout || (() => {});
    this._lastActivity = 0;
    this._timer = null;
    this._running = false;
  }

  start() {
    if (this._running) return;
    this._running = true;
    this._lastActivity = Date.now();
    this._reschedule();
  }

  stop() {
    this._running = false;
    if (this._timer) clearTimeout(this._timer);
    this._timer = null;
  }

  reset() { this._lastActivity = Date.now(); }

  markActivity(reason) {
    if (!this._running) this.start();
    this._lastActivity = Date.now();
    this._reschedule();
  }

  _reschedule() {
    if (this._timer) clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      const idleFor = Date.now() - this._lastActivity;
      if (idleFor < this.cutoffMs) {
        this._reschedule();
        return;
      }
      this._running = false;
      try { this.onTimeout({ idleFor, reason: 'silence_cutoff' }); } catch (_) {}
    }, this.cutoffMs);
  }
}

function sessionTimer() {
  if (!_instance) _instance = new SessionTimer();
  return _instance;
}

module.exports = { sessionTimer, SessionTimer, SILENCE_CUTOFF_MS, CHIME_MS };
