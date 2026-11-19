// Pure logic of the virtual radio widget (design 5.6), kept free of the DOM so it is unit tested.
// SAFETY-RELEVANT UI rules, enforced here and in radio_widget.js:
//  - Nothing is displayed from what this client sent. The widget shows the state the core reports
//    (`rc` push messages); a click only sends a request.
//  - The widget never decides what a control means. Locks come from the radio config (`lock`).

export const HEARTBEAT_MS = 300; // connection-lease heartbeat (the core needs one at least every 1 s)
export const MOMENTARY_REPEAT_MS = 100; // SE=1 refresh while pressed (the core lapses a press after 300 ms)
export const SLIDER_INTERVAL_MS = 50; // slider updates are throttled to 20 per second
export const UNLOCK_HOLD_MS = 1000; // hold the padlock this long to unlock a locked control

/** Timers that can be replaced by a fake in tests. */
export const realTimers = {
  now: () => performance.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (id) => clearTimeout(id),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (id) => clearInterval(id),
};

// ------------------------------------------------------------------ control positions

/** Nearest configured position for a pulse width (mirror of ControlSpec.from_us in the core). */
export function positionFromUs(spec, us) {
  if (spec.kind === "slider") {
    const [lo, hi] = spec.range_us || [1000, 2000];
    return Math.min(1, Math.max(0, (us - lo) / (hi - lo)));
  }
  const vals = spec.values_us || [];
  let best = 0;
  for (let i = 1; i < vals.length; i++) if (Math.abs(vals[i] - us) < Math.abs(vals[best] - us)) best = i;
  return best;
}

/** Display state for a recorded or mirrored channel vector. */
export function stateFromChannels(controls, ch) {
  const out = {};
  for (const c of controls) if (Array.isArray(ch) && c.channel <= ch.length) out[c.id] = positionFromUs(c, ch[c.channel - 1]);
  return out;
}

/** toggle2: a click flips between the two positions. */
export const nextToggle = (current) => (Number(current) === 0 ? 1 : 0);

/** switch3 (and any n-position switch): the top of the control is the highest position. `frac` 0 = top edge. */
export function switchPosFromFraction(frac, n) {
  const f = Math.min(0.999999, Math.max(0, frac));
  return n - 1 - Math.floor(f * n);
}

/** Keyboard / cycle fallback. */
export const cyclePosition = (current, n) => (Number(current) + 1) % n;

/** 0..1 slider value from a pointer x within a track. */
export function sliderFromPointer(left, width, x) {
  return width > 0 ? Math.min(1, Math.max(0, (x - left) / width)) : 0;
}

// ------------------------------------------------------------------ modes and banners

export const POCKET_WARNING = "Pocket also active: the radio rejects ground-station controls";

/**
 * Banner and capabilities from what the core reports. `rc` is the last `rc` message (or null).
 * `can_control`: the core said this client may take control (control host). `view`: "live" or "replay".
 */
export function describeRadio({ apiState, view, rc, canControl, cfg }) {
  const none = { tone: "dim", role: "none", roleText: "", warning: null, text: "", interactive: false, canTake: false, canRelease: false, holder: false };
  if (apiState !== "open") return { ...none, tone: "bad", text: "DISCONNECTED · nothing is sent (the core stops sending within 1 s)" };
  if (view === "replay") return { ...none, tone: "replay", text: "REPLAY · recorded RC state, read-only" };
  if (!rc) return { ...none, text: "RC not available in this core" };
  const holder = !!rc.holder_is_you;
  const lease = rc.lease;
  const rate = cfg && cfg.send_rate_hz ? ` · ${cfg.send_rate_hz} Hz` : "";
  switch (rc.mode) {
    case "master": {
      const warning = rc.pocket_active ? POCKET_WARNING : null;
      const m = { ...none, role: "master", roleText: "MASTER", warning };
      if (holder) {
        return { ...m, tone: "controlling", text: `MASTER · sending to ${rc.target || "?"}${rate}`, interactive: true, canRelease: true, holder };
      }
      return { ...m, tone: "dim", text: `MASTER (${lease ? lease.holder : "another client"}) · read-only` };
    }
    case "slave":
      // The Pocket's positions, read-only; nothing is sent. Switching to master is the user's choice.
      return { ...none, role: "slave", roleText: "SLAVE", tone: "mirror", text: "SLAVE · showing the Pocket · read-only", canTake: !!canControl && !lease };
    default:
      if (lease && holder) {
        return { ...none, role: "idle", roleText: "IDLE", tone: "warn", text: "MASTER REQUESTED · no RC-capable source connected · NOT SENDING", canRelease: true, holder };
      }
      if (lease) return { ...none, role: "idle", roleText: "IDLE", tone: "dim", text: `NOT SENDING · lease held by ${lease.holder}` };
      return { ...none, role: "idle", roleText: "IDLE", tone: "idle", text: "NOT SENDING", canTake: !!canControl };
  }
}

/** Padlock shown for a lockable control while the holder is operating and the core reports it locked. */
export function showPadlock(banner, lockInfo) {
  return !!(banner.interactive && lockInfo && lockInfo.locked);
}

/** A control can be operated only while interactive, and a lockable one only while unlocked. */
export function controlEnabled(banner, spec, lockInfo) {
  if (!banner.interactive) return false;
  if (spec.lock) return !!lockInfo && !lockInfo.locked;
  return true;
}

/** "unlocked 42 s": remaining time from the core's report, counted down locally between pushes. */
export function unlockRemaining(lockInfo, reportedAtMs, nowMs) {
  if (!lockInfo || lockInfo.locked || lockInfo.remaining_s == null) return null;
  return Math.max(0, lockInfo.remaining_s - (nowMs - reportedAtMs) / 1000);
}

/** Replacing the view with the replay while holding the lease needs a prompt. */
export const needsReleasePrompt = (next, rc) => next === "replay" && !!rc && !!rc.holder_is_you && !!rc.lease;

// ------------------------------------------------------------------ timing helpers

/** At most one send per `intervalMs`; the newest value always gets sent (trailing edge). */
export class Throttle {
  constructor(intervalMs, send, timers = realTimers) {
    this.intervalMs = intervalMs;
    this.send = send;
    this.t = timers;
    this.last = -Infinity;
    this.timer = null;
    this.pending = undefined;
  }

  push(value) {
    const now = this.t.now();
    if (now - this.last >= this.intervalMs && this.timer === null) {
      this.last = now;
      this.pending = undefined;
      this.send(value);
      return;
    }
    this.pending = value;
    if (this.timer === null) {
      const wait = Math.max(0, this.intervalMs - (now - this.last));
      this.timer = this.t.setTimeout(() => {
        this.timer = null;
        this.flush();
      }, wait);
    }
  }

  flush() {
    if (this.pending === undefined) return;
    const v = this.pending;
    this.pending = undefined;
    this.last = this.t.now();
    this.send(v);
  }

  cancel() {
    if (this.timer !== null) this.t.clearTimeout(this.timer);
    this.timer = null;
    this.pending = undefined;
  }
}

/**
 * Momentary control (SE): `send(1)` at once on press and again every 100 ms while held, `send(0)` on
 * release. The core also releases it by itself 300 ms after the last refresh (lost pointerup, crash).
 */
export class MomentaryHold {
  constructor(send, timers = realTimers, repeatMs = MOMENTARY_REPEAT_MS) {
    this.send = send;
    this.t = timers;
    this.repeatMs = repeatMs;
    this.timer = null;
  }

  get pressed() {
    return this.timer !== null;
  }

  press() {
    if (this.timer !== null) return;
    this.send(1);
    this.timer = this.t.setInterval(() => this.send(1), this.repeatMs);
  }

  /** Always safe to call: sends the release only if it was pressed. */
  release() {
    if (this.timer === null) return false;
    this.t.clearInterval(this.timer);
    this.timer = null;
    this.send(0);
    return true;
  }
}

/** Press-and-hold confirmation (padlock): `onDone` fires after `holdMs` of continuous holding. */
export class HoldTimer {
  constructor(onDone, timers = realTimers, holdMs = UNLOCK_HOLD_MS) {
    this.onDone = onDone;
    this.t = timers;
    this.holdMs = holdMs;
    this.timer = null;
    this.startedAt = null;
  }

  get holding() {
    return this.startedAt !== null;
  }

  start() {
    if (this.holding) return;
    this.startedAt = this.t.now();
    this.timer = this.t.setTimeout(() => {
      this.timer = null;
      this.startedAt = null;
      this.onDone();
    }, this.holdMs);
  }

  cancel() {
    if (this.timer !== null) this.t.clearTimeout(this.timer);
    this.timer = null;
    this.startedAt = null;
  }

  /** 0..1 for the progress indicator. */
  progress() {
    return this.startedAt === null ? 0 : Math.min(1, (this.t.now() - this.startedAt) / this.holdMs);
  }
}
