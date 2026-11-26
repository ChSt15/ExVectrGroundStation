// Replay playhead (docs/gui.md "Replay smoothness"): the replay position the GUI shows, extrapolated between the core's
// status pushes (every 500 ms) from the last pushed position, the speed and a monotonic clock, corrected gently when
// the next push arrives. No DOM, no clock of its own (the caller passes `now` in ms, performance.now()), unit tested
// in web/test/replay_playhead.test.js. Display only: the core owns the replay position.

export const SNAP_S = 1.0; // a push further off than this (log seconds) is a jump: snap
export const CORR_S = 0.5; // a smaller error is absorbed over this time by a temporary rate change
export const HOLD_FINAL_MS = 300; // pushes are ignored this long after a seek ...
export const HOLD_DRAG_MS = 1000; // ... and while a drag is going on (renewed by each drag step, expires on its own)

export class Playhead {
  constructor() {
    this.pos = null; // log seconds at `at`
    this.at = 0; // ms
    this.rate = 0; // log seconds per wall second while playing
    this.playing = false;
    this.corr = 0; // extra rate while correcting
    this.corrUntil = 0; // ms
    this.holdUntil = 0; // ms: pushes are ignored before this
    this.dragging = false;
    this.range = null; // [t_start, t_end]: the position never leaves it
  }

  /** Position (log seconds) at `now` ms, or null before the first push. */
  position(now) {
    if (this.pos === null) return null;
    if (!this.playing || this.isDragging(now)) return this.pos;
    const dt = Math.max(0, now - this.at) / 1000;
    const corrDt = Math.max(0, Math.min(now, this.corrUntil) - this.at) / 1000;
    let p = this.pos + this.rate * dt + this.corr * corrDt;
    if (this.range) p = Math.max(this.range[0], Math.min(this.range[1], p));
    return p;
  }

  /** True while the user is dragging (the last seek step was not final and has not expired). */
  isDragging(now) {
    return this.dragging && now < this.holdUntil;
  }

  /**
   * A status push { t, speed, state, t_start, t_end } (log seconds). Returns false when ignored (during a drag and
   * shortly after a seek). Snaps on pause, state change, a jump, a null position; otherwise corrects gently: the
   * position never goes backwards while playing, a late display is caught up and an early one slowed down.
   */
  push(rs, now) {
    if (!rs || typeof rs.t !== "number") {
      this.pos = null;
      this.playing = false;
      return true;
    }
    if (Number.isFinite(rs.t_start) && Number.isFinite(rs.t_end)) this.range = [rs.t_start, rs.t_end];
    if (now < this.holdUntil) return false;
    this.dragging = false;
    const playing = rs.state === "playing";
    const rate = Number(rs.speed) || 1;
    const cur = this.position(now);
    const snap = cur === null || !playing || !this.playing || Math.abs(rs.t - cur) > SNAP_S * Math.max(1, rate);
    if (snap) {
      this.pos = rs.t;
      this.at = now;
      this.corr = 0;
      this.corrUntil = 0;
    } else {
      const e = rs.t - cur; // positive: the display is behind
      this.pos = cur;
      this.at = now;
      this.corr = Math.max(-0.5 * rate, Math.min(rate, e / CORR_S));
      this.corrUntil = now + CORR_S * 1000;
    }
    this.rate = rate;
    this.playing = playing;
    return true;
  }

  /** A seek by the user (optimistic): show `t` now. `final` false while dragging (no extrapolation then). */
  set(t, final, now) {
    this.pos = this.range ? Math.max(this.range[0], Math.min(this.range[1], t)) : t;
    this.at = now;
    this.corr = 0;
    this.corrUntil = 0;
    this.dragging = !final;
    this.holdUntil = now + (final ? HOLD_FINAL_MS : HOLD_DRAG_MS);
  }
}

/**
 * Throttle with coalescing: `call(v, final)` runs `send(v)` at most once per `minMs`, the newest value is never lost
 * (a trailing send), a final call sends at once and cancels the pending one. `timers` ({ now, set, clear }) is
 * injectable for tests.
 */
export function createCoalescer(send, minMs, timers = { now: () => performance.now(), set: (f, ms) => setTimeout(f, ms), clear: (i) => clearTimeout(i) }) {
  let last = -Infinity;
  let timer = null;
  let pending;
  const fire = () => {
    timer = null;
    last = timers.now();
    send(pending);
  };
  return {
    call(v, final = false) {
      pending = v;
      if (timer !== null) {
        timers.clear(timer);
        timer = null;
      }
      const wait = minMs - (timers.now() - last);
      if (final || wait <= 0) fire();
      else timer = timers.set(fire, wait);
    },
    cancel() {
      if (timer !== null) timers.clear(timer);
      timer = null;
    },
  };
}

/** Debounce: `call()` runs `fn` once, `ms` after the last call (a quiet period). Injectable timers like above. */
export function createDebounce(fn, ms, timers = { set: (f, ms) => setTimeout(f, ms), clear: (i) => clearTimeout(i) }) {
  let timer = null;
  return {
    call() {
      if (timer !== null) timers.clear(timer);
      timer = timers.set(() => {
        timer = null;
        fn();
      }, ms);
    },
    cancel() {
      if (timer !== null) timers.clear(timer);
      timer = null;
    },
  };
}
