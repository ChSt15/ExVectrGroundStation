// Position trail of the 3D view (design 5.5). Pure data structures, unit tested (web/test/trail.test.js).
// Positions are NWU metres. The trail has no time limit: it is thinned by distance / time on the way in and, when it
// grows past `maxPoints`, the OLD part is decimated (every second point of the older half), never dropped. The
// `windowS` of the config (`trail_window_s`) only sets the colour fade (newest bright, older dimmer, floor 20 %).

import { NEW_MISSION_MODES, restartKind } from "../status/mission_clock.js";
export { NEW_MISSION_MODES };

/**
 * New mission detection: the shared rules of status/mission_clock.js `restartKind` (mode leaves Idle or Finished for
 * Initialisation / Startup / Running, or t_mission jumps from >= 0 back into a countdown, or the phase goes back to the
 * first phase). prev / cur: { mode, tMission, phase? } from MissionState.
 */
export function isNewMission(prev, cur) {
  if (!prev || !cur) return false;
  return restartKind({ mode: prev.mode, tm: prev.tMission, phase: prev.phase }, { mode: cur.mode, tm: cur.tMission, phase: cur.phase }) !== null;
}

const okPoint = (t, p) => Number.isFinite(t) && Array.isArray(p) && p.length === 3 && p.every(Number.isFinite);
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

export class Trail {
  /**
   * windowS: colour fade window. minDist (m) / minDt (s): a sample is added when the ship moved more than minDist or
   * minDt passed since the last stored one. maxPoints: vertex cap (old segments are decimated beyond it).
   */
  constructor(windowS = 60, { minDist = 0.03, minDt = 0.1, maxPoints = 300000 } = {}) {
    this.windowS = windowS;
    this.minDist = minDist;
    this.minDt = minDt;
    this.maxPoints = maxPoints;
    this.pts = []; // { t, p: [x, y, z] }
    this.version = 0; // increments on every change (the view redraws only then)
  }

  clear() {
    this.pts = [];
    this.version++;
  }

  /**
   * Replace all points from a get_history series { t: [..], pos: [[x,y,z], ..] } (null entries skipped), thinned by the
   * same rule as live samples. `from`: drop samples with t < from (a cleared path or a mission start).
   */
  load(series, from = -Infinity) {
    this.pts = [];
    const t = series?.t || [], pos = series?.pos || [];
    const raw = [];
    for (let i = 0; i < t.length; i++) if (okPoint(t[i], pos[i]) && t[i] >= from) raw.push({ t: t[i], p: pos[i] });
    raw.sort((a, b) => a.t - b.t);
    for (const q of raw) this._push(q.t, q.p);
    this.version++;
  }

  /**
   * Append a live sample. Returns "added", "skipped" (too close in time and space) or "reset" (time went backwards,
   * for example after a replay seek: the trail was cleared, holds this one point, and the caller should reload it).
   */
  add(t, p) {
    if (!okPoint(t, p)) return "skipped";
    const last = this.pts[this.pts.length - 1];
    if (last && t < last.t) {
      this.pts = [{ t, p: [...p] }];
      this.version++;
      return "reset";
    }
    if (!this._push(t, p)) return "skipped";
    this.version++;
    return "added";
  }

  /** Drop points newer than t (replay seek back inside an already loaded trail). */
  truncateAfter(t) {
    let n = this.pts.length;
    while (n > 0 && this.pts[n - 1].t > t) n--;
    if (n !== this.pts.length) {
      this.pts.length = n;
      this.version++;
    }
  }

  _push(t, p) {
    const last = this.pts[this.pts.length - 1];
    if (last && t - last.t < this.minDt && dist(last.p, p) <= this.minDist) return false;
    this.pts.push({ t, p: [...p] });
    if (this.pts.length > this.maxPoints) this._decimate();
    return true;
  }

  /** Over the cap: thin the older half to every second point (the first point and the recent half stay as they are). */
  _decimate() {
    const half = Math.floor(this.pts.length / 2);
    const out = [];
    for (let i = 0; i < half; i++) if (i % 2 === 0) out.push(this.pts[i]);
    this.pts = out.concat(this.pts.slice(half));
  }

  /**
   * Fade per point 1 (age 0) .. 0 (window edge and older). Points newer than `now` (entire-path mode, the part of the
   * recording after the replay position) get 0.
   */
  fades(now) {
    return this.pts.map((q) => (q.t > now ? 0 : Math.min(1, Math.max(0, 1 - (now - q.t) / this.windowS))));
  }
}

/**
 * Replay "path up to now": the trail from the start of the recording, or of the current mission, up to `now`.
 * posSeries: get_history series of Position.pos; missionSeries: { t, mission_mode, t_mission } (may be empty).
 * minT: also drop samples before it (a path the operator cleared). Returns { trail, missionStart } (missionStart = time of
 * the last new mission at or before now, or null).
 */
export function pathUpToNow(posSeries, missionSeries, now, trail, minT = -Infinity) {
  const start = lastMissionStart(missionSeries, now);
  const t = [], pos = [];
  const st = posSeries?.t || [];
  for (let i = 0; i < st.length; i++) {
    if (st[i] <= now) {
      t.push(st[i]);
      pos.push(posSeries.pos[i]);
    }
  }
  trail.load({ t, pos }, Math.max(start ?? -Infinity, minT));
  return { trail, missionStart: start };
}

/** Replay "entire path": every sample of the recording, regardless of the replay position. */
export function entirePath(posSeries, trail) {
  trail.load(posSeries);
  return trail;
}

/** Time of the last new mission (see isNewMission) among the MissionState samples with t <= now, else null. */
export function lastMissionStart(missionSeries, now) {
  const t = missionSeries?.t || [];
  let prev = null, start = null;
  for (let i = 0; i < t.length; i++) {
    if (t[i] > now) break;
    const cur = { mode: missionSeries.mission_mode?.[i], tMission: missionSeries.t_mission?.[i] };
    if (isNewMission(prev, cur)) start = t[i];
    prev = cur;
  }
  return start;
}

export const TRAIL_LS_KEY = "exgs.trail";
export const REPLAY_MODES = [["upto", "Path up to now"], ["entire", "Entire path"]];

/** Validate untrusted (saved) settings: unknown values fall back to "upto". */
export function cleanTrailSettings(x) {
  const o = x && typeof x === "object" ? x : {};
  return { replayMode: o.replayMode === "entire" ? "entire" : "upto" };
}
