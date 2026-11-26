// Mission-time display feed (docs/gui.md "Mission"): the Mission panel shows the CONTINUOUS mission time of
// status/mission_clock.js (T- during the countdown, T0 at mission begin, continuous over all phases), not the vehicle's
// per-step MissionState.t_mission (kept as the separate "phase time"). Pure, no DOM, unit tested in
// web/test/mission_time_display.test.js. Display only.
import { MissionClock } from "./mission_clock.js";
import { SmoothClock } from "./mission_smooth.js";

const SAMPLE_GAP_S = 0.9; // at most about one sample per second, plus every phase change (as the events list does)

export class MissionTimeFeed {
  constructor() {
    this.clock = new MissionClock();
    this.smooth = new SmoothClock();
    this.lastEl = undefined;
    this.reset();
  }

  reset() {
    this.clock.clear();
    this.lastSample = -Infinity;
    this.lastPhase = undefined;
    this.lastMode = undefined;
    this.smooth.reset();
  }

  /** One MissionState packet ({t, f}); other types are ignored. Returns true when the clock took a sample. */
  feed(pkt) {
    if (!pkt || pkt.type !== "MissionState" || !pkt.f || typeof pkt.t !== "number") return false;
    const phase = pkt.f.mission_phase;
    const mode = pkt.f.mission_mode;
    if (pkt.t - this.lastSample < SAMPLE_GAP_S && phase === this.lastPhase && mode === this.lastMode) return false;
    this.lastPhase = phase;
    this.lastMode = mode;
    if (!this.clock.add(pkt.t, pkt.f.t_mission ?? null, phase, mode, pkt.f.mission_elapsed)) return false;
    this.lastSample = pkt.t;
    return true;
  }

  /**
   * LIVE telemetry value (every MissionState packet, not thinned) for the smooth counter; `nowMs` = performance.now().
   * The panel feeds each live packet here and shows `displayTime`.
   */
  live(pkt, nowMs) {
    if (!pkt || pkt.type !== "MissionState" || !pkt.f) return;
    const el = pkt.f.mission_elapsed;
    if (typeof el !== "number" || !Number.isFinite(el)) {
      if (this.clock.source === "telemetry" || el === null) this.smooth.reset();
      return;
    }
    this.smooth.push(el, this.clock.running(), nowMs);
  }

  feedAll(packets) {
    for (const p of [...packets].sort((a, b) => a.t - b.t)) this.feed(p);
  }

  /** "telemetry" (the vehicle's mission_elapsed) or "derived" (inferred from t_mission / phase / mode, old data). */
  get source() {
    return this.clock.source;
  }

  /**
   * The value the Mission panel shows. Live with telemetry: the smooth counter (nowMs = performance.now()); replay: the
   * recorded samples interpolated at the replay position `replayT` (falls back to the entry's sample time);
   * derived data: the mission time at the entry's sample time.
   */
  displayTime(entry, { live = true, nowMs = 0, replayT = null } = {}) {
    if (live && this.clock.source === "telemetry") {
      const v = this.smooth.value(nowMs);
      if (v !== null) return v;
    }
    if (!live && Number.isFinite(replayT)) return this.clock.missionTime(replayT);
    return this.missionTime(entry);
  }

  /** Mission time (s) at the store entry's sample time, or null when no mission has been seen (shown as an em dash). */
  missionTime(entry) {
    return entry && typeof entry.t === "number" ? this.clock.missionTime(entry.t) : null;
  }
}
