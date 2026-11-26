// Mission time axis of the tuning graphs (docs/gui.md "Tuning view"): tick labels from the continuous mission clock
// (../status/mission_clock.js, T0 = mission begin, not the per-step t_mission). No DOM, unit tested in
// web/test/zoom.test.js.
import { niceTicks, tickDigits } from "./series.js";
export { MissionClock } from "../status/mission_clock.js";

/** "T-30", "T+12.5", "T0": signed mission seconds with `digits` decimals. */
export function fmtT(tm, digits = 0) {
  if (!Number.isFinite(tm)) return "—";
  const a = Math.abs(tm);
  const s = a.toFixed(digits);
  if (Number(s) === 0) return "T0";
  return `T${tm < 0 ? "-" : "+"}${s}`;
}

export const MISSION_TITLE = "mission time (s)";
export const MISSION_TIP = "x axis: mission time in seconds from T0 = mission begin (T- before, T+ after)";

/**
 * x axis of a window [t0, t1]: { mode: "mission", ticks: [{ t, label }], title, tip, label(t) } when the window has a
 * mission clock, else { mode: "relative" } (the caller keeps its own relative labels).
 */
export function missionAxis(clock, t0, t1, wantTicks = 6) {
  if (!clock.covers(t0, t1)) return { mode: "relative" };
  const m0 = clock.missionTime(t0);
  const m1 = clock.missionTime(t1);
  const label = (t, digits = 2) => {
    const tm = clock.missionTime(t);
    return tm === null ? "—" : fmtT(tm, digits);
  };
  const raw = niceTicks(Math.min(m0, m1), Math.max(m0, m1), wantTicks);
  const digits = tickDigits(raw);
  const mid = (t0 + t1) / 2;
  const ticks = [];
  for (const tm of raw) {
    const t = clock.toSample(tm, mid);
    if (t !== null && t >= t0 && t <= t1) ticks.push({ t, label: fmtT(tm, digits) });
  }
  return { mode: "mission", ticks, title: MISSION_TITLE, tip: MISSION_TIP, label };
}
