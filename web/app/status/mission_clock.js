// One continuous MISSION clock from the MissionState samples (docs/gui.md "Tuning view", "x axis"). Pure, no DOM, unit
// tested in web/test/mission_clock.test.js. Display only.
//
// MissionState.t_mission restarts with every step of the mission chain (waypoint, bellyflop, descent, ...), so it is not
// the time since mission begin. The mission clock is built from the history of samples { t, tm, phase } (t = sample
// time in s, tm = t_mission in s or null / NaN, phase = MissionPhase name or undefined), not from a running
// accumulator, so it is the same live, in replay and after a seek:
//   - A mission starts with the first sample of a countdown (tm < 0) when no mission is running (none yet, or the last
//     one ended). T0 = t - tm of the first sample with tm >= 0 (the moment the first step's clock reaches 0); during the
//     countdown T0 is projected from the latest sample (t - tm), so T-minus counts down correctly while it runs.
//   - After T0 the mission time is simply sample time - T0 across ALL steps. A restart or a negative tm inside a running
//     mission (a step change) does not start a new mission.
//   - A mission ends when the phase goes to Completed ("completed"), when it goes back to Idle from a running phase
//     ("reset"), or when the clock is null or the samples stop for more than GAP_S ("clock lost"; the end is the last sample
//     with a clock; if the clock returns inside a running phase the old T0 keeps applying). A new countdown after an end starts the next mission with its own T0.
//   - If the first sample of the data already has tm >= 0 in a running phase (history starts mid-mission) a mission
//     starts there with T0 = t - tm and `partial: true` (that step's clock is all that is known).
// missionTime(t) = t - T0 of the last mission that started at or before t (the first one for earlier times, so the
// countdown before the first sample extrapolates); null when no mission exists.

export const GAP_S = 5; // a clock gap longer than this ends a mission
const POST_S = 120; // `covers`: how long after its end a mission still labels a window

const finite = (v) => typeof v === "number" && Number.isFinite(v);

/**
 * Missions from time-ordered samples. Returns [{ start, t0, t0Known, end, endReason, partial }]: start = time of the first
 * sample, t0 = sample time of mission time 0 (projected until t0Known), end = null while running, endReason
 * "completed" | "reset" | "clock lost" | null.
 */
export function buildMissions(samples) {
  const missions = [];
  let cur = null; // running mission
  let lastGood = null; // time of the last sample with a clock
  let nullSince = null;
  let prevPhase;
  let prevT = null;
  const finish = (end, reason) => {
    cur.end = end;
    cur.endReason = reason;
    cur = null;
  };
  for (const s of samples) {
    const phase = s.phase;
    const running = (p) => p !== undefined && p !== null && p !== "Idle" && p !== "Completed";
    const ok = finite(s.tm);
    if (cur && prevT !== null && s.t - prevT > GAP_S) {
      finish(lastGood, "clock lost"); // no samples for a while (link drop, seek in a recording)
      nullSince = null;
    }
    prevT = s.t;
    if (cur) {
      if (phase === "Completed" && prevPhase !== "Completed") finish(s.t, "completed");
      else if (phase === "Idle" && running(prevPhase)) finish(s.t, "reset");
    }
    if (!ok) {
      if (cur && nullSince === null) nullSince = s.t;
      if (cur && s.t - nullSince > GAP_S) finish(lastGood, "clock lost");
      prevPhase = phase ?? prevPhase;
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
      cur = { start: s.t, t0: s.t - s.tm, t0Known: false, end: null, endReason: null, partial: false };
      missions.push(cur);
    } else if (!missions.length && running(phase) && prevPhase !== "Completed") {
      // data begins mid-mission
      cur = { start: s.t, t0: s.t - s.tm, t0Known: true, end: null, endReason: null, partial: true };
      missions.push(cur);
    }
    prevPhase = phase ?? prevPhase;
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

  /** One MissionState sample in time order (older ones are dropped): t seconds, tm = t_mission or null, phase name. */
  add(t, tm, phase) {
    const n = this.samples.length;
    if (n && t < this.samples[n - 1].t) return false;
    this.samples.push({ t, tm: finite(tm) ? tm : null, phase: typeof phase === "string" ? phase : undefined });
    if (this.samples.length > this.cap) this.samples.splice(0, this.samples.length - this.cap);
    this._missions = null;
    return true;
  }

  /** [{ start, t0, t0Known, end, endReason, partial }], see buildMissions. */
  get missions() {
    return (this._missions ??= buildMissions(this.samples));
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
    return m ? t - m.t0 : null;
  }

  /** Sample time of mission time tm of the mission that `hint` (a sample time) belongs to; null without a mission. */
  toSample(tm, hint) {
    let t = hint;
    for (let k = 0; k < 3; k++) {
      const m = this._of(t);
      if (!m) return null;
      t = tm + m.t0;
    }
    return t;
  }

  /** True when a mission is running in, or just ended before, the window [t0, t1] (else the axis is not mission time). */
  covers(t0, t1) {
    return this.missions.some((m) => m.start <= t1 && (m.end === null || m.end + POST_S >= t0));
  }
}
