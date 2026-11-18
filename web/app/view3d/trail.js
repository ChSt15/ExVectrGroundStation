// Position trail (design 5.5): last `windowS` seconds of view time, from history plus live packets.
// Pure data structure, unit tested. Positions are NWU metres.

export class Trail {
  constructor(windowS = 60, { minDt = 0.05, maxPoints = 4000 } = {}) {
    this.windowS = windowS;
    this.minDt = minDt;
    this.maxPoints = maxPoints;
    this.pts = []; // { t, p: [x, y, z] }
  }

  clear() {
    this.pts = [];
  }

  /** Replace all points from a get_history series: { t: [..], pos: [[x,y,z], ..] } (null entries are skipped). */
  load(series) {
    this.pts = [];
    const t = series?.t || [], pos = series?.pos || [];
    for (let i = 0; i < t.length; i++) {
      const p = pos[i];
      if (Number.isFinite(t[i]) && Array.isArray(p) && p.length === 3 && p.every(Number.isFinite)) this.pts.push({ t: t[i], p: [...p] });
    }
    this.pts.sort((a, b) => a.t - b.t);
    this._cap();
  }

  /**
   * Append a live sample. Returns "added", "skipped" (too close in time) or "reset" (time went backwards,
   * for example after a replay seek: the trail was cleared and the caller should reload it from history).
   */
  add(t, p) {
    if (!Number.isFinite(t) || !Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite)) return "skipped";
    const last = this.pts[this.pts.length - 1];
    if (last && t < last.t) {
      this.pts = [];
      this.pts.push({ t, p: [...p] });
      return "reset";
    }
    if (last && t - last.t < this.minDt) return "skipped";
    this.pts.push({ t, p: [...p] });
    this._cap();
    return "added";
  }

  /** Drop points older than the window relative to `now` (view time). */
  prune(now) {
    if (!Number.isFinite(now)) return;
    let i = 0;
    while (i < this.pts.length && now - this.pts[i].t > this.windowS) i++;
    if (i) this.pts.splice(0, i);
  }

  _cap() {
    if (this.pts.length > this.maxPoints) this.pts.splice(0, this.pts.length - this.maxPoints);
  }

  /** Fade factor per point: 1 = newest (age 0), 0 = at the window edge. */
  fades(now) {
    return this.pts.map((q) => Math.min(1, Math.max(0, 1 - (now - q.t) / this.windowS)));
  }
}
