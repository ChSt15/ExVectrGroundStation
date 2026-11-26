// One continuous MISSION clock from the MissionState samples (docs/gui.md "Tuning view", "x axis"). Pure, no DOM, unit
// tested in web/test/mission_clock.test.js. Display only.
//
// MissionState.t_mission restarts with every step of the mission chain, so it is not the time since mission begin (the
// telemetry has no whole-mission elapsed time). The mission clock is built from the history of samples
// { t, tm, phase, mode } (t = sample time in s, tm = t_mission in s or null, phase = MissionPhase name, mode =
// MissionMode name; phase / mode may be undefined), not from a running accumulator, so it is the same live, in replay
// and after a seek:
//   - A mission starts (1) with the first sample of a countdown (tm < 0) when no mission is running, or (2) when
//     `restartKind` says a NEW mission began (below), also while the previous one is still running: the previous one then
//     ends with endReason "restarted". T0 = t - tm of the first sample with tm >= 0; during the countdown T0 is projected
//     from the latest sample (t - tm).
//   - NEW MISSION (restartKind, shared with the 3D trail): (mode) mission_mode goes from Idle or Finished to
//     Initialisation, Startup or Running; (countdown) t_mission jumps from >= 0 back to < 0 (the clock of the running
//     mission counts only when it had passed T0); (phase) mission_phase goes back to the first phase (Hover) from a later
//     phase or from Completed.
//   - After T0 the mission time is sample time - T0 across ALL steps; a step restart of t_mission to a value >= 0 does not
//     start a new mission.
//   - A mission ends when the phase goes to Completed ("completed"), back to Idle from a running phase ("reset"), when
//     a new mission starts ("restarted"), or when the clock is null / the samples stop for more than GAP_S ("clock
//     lost"; the end is the last sample with a clock; if the clock returns inside a running phase the old T0 keeps applying).
//   - If the first sample of the data already has tm >= 0 in a running phase (history starts mid-mission) a mission
//     starts there with T0 = t - tm and `partial: true` (that step's clock is all that is known).
// missionTime(t) = t - T0 of the last mission that started at or before t (the first one for earlier times, so the
// countdown reads T-).

export const GAP_S = 5; // a clock gap longer than this ends a mission
const POST_S = 120; // `covers`: how long after its end a mission still labels a window

const finite = (v) => typeof v === "number" && Number.isFinite(v);

export const NEW_MISSION_MODES = ["Initialisation", "Startup", "Running"];
const LATER_PHASES = ["Ascent", "Descent", "Deceleration", "RTH", "Landing", "Completed"];
const FIRST_PHASE = "Hover";

/**
 * The ONE place that decides "a new mission began" between two consecutive MissionState readings (also used by the 3D
 * trail). prev / cur: { mode, tm, phase } (any may be undefined / null / NaN; tm in s). Returns "mode" | "countdown" |
 * "phase" or null:
 *   mode: mission_mode Idle or Finished -> Initialisation / Startup / Running;
 *   countdown: tm >= 0 -> tm < 0;
 *   phase: mission_phase from a later phase (Ascent .. Landing, Completed) back to the first phase (Hover).
 */
export function restartKind(prev, cur) {
  if (!prev || !cur) return null;
  if ((prev.mode === "Idle" || prev.mode === "Finished") && NEW_MISSION_MODES.includes(cur.mode)) return "mode";
  if (finite(prev.tm) && finite(cur.tm) && prev.tm >= 0 && cur.tm < 0) return "countdown";
  if (cur.phase === FIRST_PHASE && LATER_PHASES.includes(prev.phase)) return "phase";
  return null;
}

export const RESET_JUMP_S = 1.0; // mission_elapsed going back by more than this = a new mission
export const EXTRAP_S = 1.0; // missionTime extrapolates at most this far beyond the newest telemetry sample of a running mission
const HOLD_S = 1.0; // mission_elapsed unchanged for longer than this = the final value is held (the mission chain ended)

/**
 * TELEMETRY clock (primary): the vehicle's own whole-mission time `mission_elapsed` (s, NaN/None = no mission), carried in
 * samples as `el`. A new mission is exactly when it resets: it jumps back by more than RESET_JUMP_S, or goes from the
 * held final value / a NaN (reset to Idle) to a negative value. T-minus, T0 and the continuity over all steps are the
 * vehicle's. A mission = { start, t0, t0Known, end, endReason, partial, telemetry: true, pts: [{ t, el }] }; t0 = the
 * sample time at which mission_elapsed crosses 0 (interpolated), projected from the latest sample (t - el) before that.
 * endReason: "held" (the final value stopped changing), "reset" (NaN again), "restarted", or null while running.
 * `partial` marks data that begins inside a running mission (T0 is still known: it comes from the value itself).
 */
export function buildTelemetryMissions(samples) {
  const missions = [];
  let cur = null;
  let prev = null; // last sample with a finite el: { t, el }
  let moveT = null; // sample time of the last change of el
  for (const s of samples) {
    if (!finite(s.el)) {
      if (cur && cur.end === null) {
        cur.end = s.t;
        cur.endReason = "reset";
      }
      continue;
    }
    const back = prev !== null && s.el < prev.el - RESET_JUMP_S;
    const afterReset = cur && cur.endReason === "reset" && s.el < 0;
    if (!cur || back || afterReset) {
      if (cur && cur.end === null) {
        cur.end = s.t;
        cur.endReason = "restarted";
      }
      cur = { start: s.t, t0: s.t - s.el, t0Known: false, end: null, endReason: null, partial: !cur && s.el >= 0, telemetry: true, pts: [] };
      missions.push(cur);
      moveT = s.t;
    } else if (s.el > prev.el + 1e-6) {
      if (cur.end !== null) {
        cur.end = null; // the "held" value moved again: not the end after all
        cur.endReason = null;
      }
      moveT = s.t;
    } else if (cur.end === null && s.el >= 0 && s.t - moveT > HOLD_S) {
      cur.end = moveT;
      cur.endReason = "held";
    }
    cur.pts.push({ t: s.t, el: s.el });
    if (!cur.t0Known) {
      cur.t0 = s.t - s.el; // projected until a sample reaches 0
      if (s.el >= 0) {
        const k = cur.pts.length - 2;
        cur.t0 = k >= 0 && cur.pts[k].el < 0 ? cur.pts[k].t + ((0 - cur.pts[k].el) * (s.t - cur.pts[k].t)) / (s.el - cur.pts[k].el) : s.t - s.el;
        cur.t0Known = true;
      }
    }
    prev = { t: s.t, el: s.el };
  }
  return missions;
}

/** Interpolated mission time (s) of mission `m` at sample time t (derived missions: t - t0). */
function timeIn(m, t) {
  if (!m.pts) return t - m.t0;
  const p = m.pts;
  if (t <= p[0].t) return p[0].el + (t - p[0].t);
  const last = p[p.length - 1];
  if (t >= last.t) return m.end !== null && m.endReason !== "restarted" ? last.el : last.el + Math.min(t - last.t, EXTRAP_S);
  let lo = 0, hi = p.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (p[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const dt = p[hi].t - p[lo].t;
  return dt > 0 ? p[lo].el + ((p[hi].el - p[lo].el) * (t - p[lo].t)) / dt : p[hi].el;
}

/** Sample time at which mission `m` reads mission time tm (the first one on a held plateau). */
function sampleIn(m, tm) {
  if (!m.pts) return tm + m.t0;
  const p = m.pts;
  if (tm <= p[0].el) return p[0].t + (tm - p[0].el);
  const last = p[p.length - 1];
  if (tm >= last.el) return last.t + (tm - last.el);
  let lo = 0, hi = p.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (p[mid].el < tm) lo = mid;
    else hi = mid;
  }
  const de = p[hi].el - p[lo].el;
  return de > 0 ? p[lo].t + ((p[hi].t - p[lo].t) * (tm - p[lo].el)) / de : p[hi].t;
}

/**
 * Missions from time-ordered samples: the telemetry clock when any sample carries a finite mission_elapsed (`el`),
 * else the DERIVED clock below (old recordings, old senders). Returns [{ start, t0, t0Known, end, endReason, partial }]: start = time of the first
 * sample, t0 = sample time of mission time 0 (projected until t0Known), end = null while running, endReason
 * "completed" | "reset" | "restarted" | "clock lost" | null.
 */
export function buildMissions(samples) {
  if (samples.some((s) => finite(s.el))) return buildTelemetryMissions(samples);
  return buildDerivedMissions(samples);
}

/** The derived clock: missions inferred from t_mission, phase and mode (restartKind). */
export function buildDerivedMissions(samples) {
  const missions = [];
  let cur = null; // running mission
  let lastGood = null; // time of the last sample with a clock
  let nullSince = null;
  let prev = { mode: undefined, tm: null, phase: undefined }; // last defined value of each field
  let prevT = null;
  const running = (p) => p !== undefined && p !== null && p !== "Idle" && p !== "Completed";
  const finish = (end, reason) => {
    cur.end = end;
    cur.endReason = reason;
    cur = null;
  };
  const begin = (s, ok, partial) => {
    cur = { start: s.t, t0: ok ? s.t - s.tm : s.t, t0Known: partial, end: null, endReason: null, partial };
    missions.push(cur);
  };
  for (const s of samples) {
    const phase = s.phase;
    const ok = finite(s.tm);
    if (cur && prevT !== null && s.t - prevT > GAP_S) {
      finish(lastGood, "clock lost"); // no samples for a while (link drop, seek in a recording)
      nullSince = null;
    }
    prevT = s.t;
    if (cur) {
      if (phase === "Completed" && prev.phase !== "Completed") finish(s.t, "completed");
      else if (phase === "Idle" && running(prev.phase)) finish(s.t, "reset");
    }
    // a new mission begins (mode / countdown / phase rule); the countdown rule counts only past T0 of the running one
    const kind = restartKind(prev, { mode: s.mode, tm: ok ? s.tm : null, phase });
    const fresh = kind !== null && !(kind === "countdown" && cur && !cur.t0Known);
    if (fresh && cur) finish(s.t, "restarted");
    if (fresh && !cur) {
      begin(s, ok, false); // t0Known only once a following sample has tm >= 0 (the first value may be stale)
      if (ok) lastGood = s.t;
      nullSince = null;
      prev = { mode: s.mode ?? prev.mode, tm: ok ? s.tm : prev.tm, phase: phase ?? prev.phase };
      continue;
    }
    if (!ok) {
      if (cur && nullSince === null) nullSince = s.t;
      if (cur && s.t - nullSince > GAP_S) finish(lastGood, "clock lost");
      prev = { mode: s.mode ?? prev.mode, tm: prev.tm, phase: phase ?? prev.phase };
      continue;
    }
    if (cur && nullSince !== null) {
      if (s.t - nullSince > GAP_S) finish(lastGood, "clock lost");
      nullSince = null;
    }
    lastGood = s.t;
    if (cur) {
      if (!cur.t0Known) {
        cur.t0 = s.t - s.tm; // countdown: projected, then fixed at the first sample with tm >= 0
        if (s.tm >= 0) cur.t0Known = true;
      }
    } else if (s.tm < 0) {
      begin(s, true, false);
    } else if (!missions.length && running(phase) && prev.phase !== "Completed") {
      begin(s, true, true); // data begins mid-mission
    }
    prev = { mode: s.mode ?? prev.mode, tm: s.tm, phase: phase ?? prev.phase };
  }
  if (cur && nullSince !== null && samples.length && samples[samples.length - 1].t - nullSince > GAP_S) finish(lastGood, "clock lost");
  return missions;
}

/** Samples in, missions and the time mapping out (recomputed from the samples when they changed). */
export class MissionClock {
  constructor(capacity = 20000) {
    this.cap = capacity;
    this.samples = [];
    this._missions = null;
  }

  clear() {
    this.samples = [];
    this._missions = null;
  }

  /** One MissionState sample in time order (older ones are dropped): t seconds, tm = t_mission or null, phase and mode names. */
  add(t, tm, phase, mode, el) {
    const n = this.samples.length;
    if (n && t < this.samples[n - 1].t) return false;
    this.samples.push({ t, tm: finite(tm) ? tm : null, phase: typeof phase === "string" ? phase : undefined, mode: typeof mode === "string" ? mode : undefined, el: finite(el) ? el : null });
    if (this.samples.length > this.cap) this.samples.splice(0, this.samples.length - this.cap);
    this._missions = null;
    return true;
  }

  /** [{ start, t0, t0Known, end, endReason, partial }], see buildMissions. */
  get missions() {
    return (this._missions ??= buildMissions(this.samples));
  }

  /** "telemetry" when the vehicle's mission_elapsed is in the data (primary), else "derived" (inferred, old data). */
  get source() {
    return this.samples.some((s) => finite(s.el)) ? "telemetry" : "derived";
  }

  /** Mission time (s) of mission `m` (from `missions`) at sample time t: interpolated telemetry, or t - T0 when derived. */
  timeIn(m, t) {
    return timeIn(m, t);
  }

  /** Sample time at which mission `m` reads mission time tm. */
  sampleIn(m, tm) {
    return sampleIn(m, tm);
  }

  /** True while the latest mission is running (not ended); false when none or ended (a held final value is frozen). */
  running() {
    const ms = this.missions;
    return ms.length > 0 && ms[ms.length - 1].end === null;
  }

  /** Mission starts: [{ t0, end, endReason, partial, known }] (T0 times with their ends; end null = running). */
  starts() {
    return this.missions.map((m) => ({ t0: m.t0, end: m.end, endReason: m.endReason, partial: m.partial, known: m.t0Known }));
  }

  _of(t) {
    const ms = this.missions;
    let k = 0;
    for (let i = 0; i < ms.length; i++) if (ms[i].start <= t) k = i;
    return ms.length ? ms[k] : null;
  }

  /** Mission time (s, negative before T0) at sample time t, or null when there is no mission. */
  missionTime(t) {
    const m = this._of(t);
    return m ? timeIn(m, t) : null;
  }

  /**
   * The mission a window [t0, t1] belongs to for the graph axis: the one whose span (start until the next one starts)
   * overlaps the window most (a tie goes to the later one); null without a mission.
   */
  dominant(t0, t1) {
    const ms = this.missions;
    if (!ms.length) return null;
    let best = this._of(t0);
    let bestOv = -1;
    ms.forEach((m, i) => {
      const lo = Math.max(t0, i === 0 ? -Infinity : m.start);
      const hi = Math.min(t1, i + 1 < ms.length ? ms[i + 1].start : Infinity);
      const ov = hi - lo;
      if (ov > 0 && ov >= bestOv) {
        best = m;
        bestOv = ov;
      }
    });
    return best;
  }

  /** Sample time of mission time tm of the mission that `hint` (a sample time) belongs to; null without a mission. */
  toSample(tm, hint) {
    let t = hint;
    for (let k = 0; k < 3; k++) {
      const m = this._of(t);
      if (!m) return null;
      t = sampleIn(m, tm);
    }
    return t;
  }

  /** True when a mission is running in, or just ended before, the window [t0, t1] (else the axis is not mission time). */
  covers(t0, t1) {
    return this.missions.some((m) => m.start <= t1 && (m.end === null || m.end + POST_S >= t0));
  }
}
