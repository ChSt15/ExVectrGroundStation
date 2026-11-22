// Vehicle log watcher: turns ConsoleText lines into notification events, keeps the calibration problem chip state and the
// unread count of the Console tab. Pure logic with an injected clock (unit tested in web/test/logwatch.test.js).
// Display only: nothing is sent to the vehicle. The Console keeps every line; this is never the only warning.
import { calibrationProblem, chipText, classify } from "./log_patterns.js";

export const DEDUPE_S = 10; // identical text within this time: one toast with a count
export const CHIP_STALE_S = 25; // the vehicle repeats its calibration warning every 10 s

export class LogWatcher {
  /**
   * `emit(entry, repeat)` is called for each line that should raise a notification (settings are checked by the
   * caller); `repeat` is true for an identical text within DEDUPE_S (the caller bumps the count of a visible toast).
   */
  constructor({ clock = () => performance.now() / 1000, emit = () => {} } = {}) {
    this.clock = clock;
    this.emit = emit;
    this.reset();
  }

  reset() {
    this.seen = new Map(); // title -> last emit time
    this.problem = null; // {bad, detail, text, t}
    this.unreadCount = 0;
    this.unreadErrors = 0;
  }

  /** Feed one ConsoleText line. Returns the classification (or null). */
  feed(raw, { notify = true } = {}) {
    const c = classify(raw);
    if (!c) return null;
    const t = this.clock();
    if (c.isProblemLevel) {
      this.unreadCount += 1;
      if (c.level === "ERROR") this.unreadErrors += 1;
    }
    const had = this.problem ? chipText(this.problem) : null;
    if (c.calib === "ok") this.problem = null;
    else if (c.calib === "problem") {
      const p = calibrationProblem(c.text);
      this.problem = p ? { ...p, t } : { bad: [], detail: {}, t };
    }
    // the repeated warning toasts only when the problem appears or changes (the chip shows it meanwhile)
    const chipRepeat = c.category === "calibration" && c.calib === "problem" && had !== null && had === (this.problem ? chipText(this.problem) : null);
    if (!notify || chipRepeat) return c;
    const last = this.seen.get(c.title);
    const repeat = last !== undefined && t - last < DEDUPE_S;
    if (!repeat) this.seen.set(c.title, t);
    this.emit(c, repeat);
    return c;
  }

  /** Time-driven: drop a calibration problem that has not been repeated for CHIP_STALE_S. */
  tick() {
    const t = this.clock();
    if (this.problem && t - this.problem.t > CHIP_STALE_S) this.problem = null;
    if (this.seen.size > 200) for (const [k, v] of this.seen) if (t - v > DEDUPE_S) this.seen.delete(k);
  }

  chip() {
    return this.problem ? { text: chipText(this.problem), detail: this.problem.detail } : null;
  }

  unread() {
    return this.unreadCount;
  }

  markRead() {
    this.unreadCount = 0;
    this.unreadErrors = 0;
  }
}
