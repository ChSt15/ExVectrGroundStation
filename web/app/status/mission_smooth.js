// Smooth live mission counter (docs/gui.md "Mission clock"): the vehicle's mission_elapsed arrives at 2 to 5 Hz, the
// Mission panel redraws every 100 ms. The displayed value is anchored to the telemetry (value + the local monotonic clock
// in ms, performance.now()), runs at 1 s/s between samples and is corrected gently toward each new sample: an error is
// absorbed over CORR_S by a temporary rate change, the value never runs backwards, and it snaps on a reset (a jump back),
// on an error above SNAP_S, and whenever the clock stops or starts running (held final value). Without a new sample for
// MAX_RUN_S it stops extrapolating (a dead link must not look alive). Same idea as status/replay_playhead.js. Pure, no
// DOM, unit tested in web/test/mission_smooth.test.js. Display only.

export const SNAP_S = 1.0;
export const CORR_S = 0.5;
export const MAX_RUN_S = 2.0;

export class SmoothClock {
  constructor() {
    this.reset();
  }

  reset() {
    this.pos = null; // mission seconds at `at`
    this.at = 0; // ms
    this.running = false;
    this.corr = 0; // extra rate (s/s) while correcting
    this.corrUntil = 0; // ms
    this.lastEl = null;
  }

  /** Displayed mission time (s) at `now` ms, or null before the first sample. */
  value(now) {
    if (this.pos === null) return null;
    if (!this.running) return this.pos;
    const dt = Math.min(Math.max(0, now - this.at), MAX_RUN_S * 1000) / 1000;
    const cdt = Math.max(0, Math.min(now, this.corrUntil, this.at + MAX_RUN_S * 1000) - this.at) / 1000;
    return this.pos + dt + this.corr * cdt;
  }

  /**
   * A telemetry value `el` (mission_elapsed in s, or null = no mission) received at `now` ms. `running` = the clock is
   * advancing (false for a held final value). Returns the displayed value after the push.
   */
  push(el, running, now) {
    if (!Number.isFinite(el)) {
      this.reset();
      return null;
    }
    const cur = this.value(now);
    const snap = cur === null || running !== this.running || !running || Math.abs(el - cur) > SNAP_S;
    if (snap) {
      this.pos = el;
      this.corr = 0;
    } else {
      // rebase to the current display, absorb the error over CORR_S; the total rate (1 + corr) stays >= 0
      this.pos = cur;
      this.corr = Math.max(-1, (el - cur) / CORR_S);
      this.corrUntil = now + CORR_S * 1000;
    }
    this.at = now;
    this.running = running;
    this.lastEl = el;
    return this.value(now);
  }
}
