// Mission-time display feed (docs/gui.md "Mission"): the Mission panel shows the CONTINUOUS mission time of
// status/mission_clock.js (T- during the countdown, T0 at mission begin, continuous over all phases), not the vehicle's
// per-step MissionState.t_mission (kept as the separate "phase time"). Pure, no DOM, unit tested in
// web/test/mission_time_display.test.js. Display only.
import { MissionClock } from "./mission_clock.js";

const SAMPLE_GAP_S = 0.9; // at most about one sample per second, plus every phase change (as the events list does)

export class MissionTimeFeed {
  constructor() {
    this.clock = new MissionClock();
    this.reset();
  }

  reset() {
    this.clock.clear();
    this.lastSample = -Infinity;
    this.lastPhase = undefined;
  }

  /** One MissionState packet ({t, f}); other types are ignored. Returns true when the clock took a sample. */
  feed(pkt) {
    if (!pkt || pkt.type !== "MissionState" || !pkt.f || typeof pkt.t !== "number") return false;
    const phase = pkt.f.mission_phase;
    if (pkt.t - this.lastSample < SAMPLE_GAP_S && phase === this.lastPhase) return false;
    this.lastPhase = phase;
    if (!this.clock.add(pkt.t, pkt.f.t_mission ?? null, phase)) return false;
    this.lastSample = pkt.t;
    return true;
  }

  feedAll(packets) {
    for (const p of [...packets].sort((a, b) => a.t - b.t)) this.feed(p);
  }

  /** Mission time (s) at the store entry's sample time, or null when no mission has been seen (shown as an em dash). */
  missionTime(entry) {
    return entry && typeof entry.t === "number" ? this.clock.missionTime(entry.t) : null;
  }
}
