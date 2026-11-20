// Speech queue and duplicate suppression (docs/callouts.md "Priorities, queue and suppression").
// Pure apart from the injected clock (seconds) and speaker: speaker.speak(ev, done) / speaker.cancel().

/** Drops identical text inside a window (normal 2 s, failures 8 s). Repeats of a persisting failure bypass it. */
export class Suppressor {
  constructor(getSettings, clock) {
    this.cfg = getSettings;
    this.clock = clock;
    this.last = new Map();
  }

  /** True when the event may be announced; records the time. */
  accept(ev) {
    const t = this.clock();
    const win = (ev.severity === "failure" ? this.cfg().suppress.failure_s : this.cfg().suppress.normal_s) || 0;
    const prev = this.last.get(ev.text);
    if (!ev.repeat && prev !== undefined && t - prev < win) return false;
    this.last.set(ev.text, t);
    return true;
  }

  clear() {
    this.last.clear();
  }
}

export const DEFAULT_MAX_AGE_S = 6;

export class CalloutQueue {
  constructor({ speaker, clock }) {
    this.speaker = speaker;
    this.clock = clock;
    this.pending = [];
    this.current = null;
    this.token = 0;
  }

  /** Queue an event. A failure (priority 0) cancels the speech in progress and every lower-priority pending item. */
  push(ev) {
    const item = { ev, t: this.clock() };
    if (ev.priority === 0) {
      this.pending = this.pending.filter((p) => p.ev.priority === 0);
      if (this.current && this.current.ev.priority > 0) {
        this.token += 1; // the cancelled utterance's end callback must not advance the queue
        this.current = null;
        this.speaker.cancel();
      }
    }
    this.pending.push(item);
    this.pending.sort((a, b) => a.ev.priority - b.ev.priority || a.t - b.t);
    this.pump();
  }

  /** Drop everything and silence the speaker (mute, seek, audio disabled). */
  cancelAll() {
    this.pending = [];
    if (this.current) {
      this.token += 1;
      this.current = null;
      this.speaker.cancel();
    }
  }

  pump() {
    const now = this.clock();
    this.pending = this.pending.filter((p) => now - p.t <= (p.ev.maxAge ?? DEFAULT_MAX_AGE_S));
    if (this.current || !this.pending.length) return;
    const item = this.pending.shift();
    this.current = item;
    const token = ++this.token;
    this.speaker.speak(item.ev, () => {
      if (token !== this.token) return;
      this.current = null;
      this.pump();
    });
  }

  tick() {
    this.pump();
  }
}
