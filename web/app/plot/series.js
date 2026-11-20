// Pure time-series helpers of the tuning graphs (docs/gui.md "Tuning view"): a ring buffer per signal, the visible
// window, min/max decimation per pixel column, axis ticks and CSV. No DOM, unit tested in web/test/plot.test.js.

/** Fixed-capacity ring buffer of (t seconds, value) samples in time order. NaN/null values are kept as gaps. */
export class RingSeries {
  constructor(capacity = 16384) {
    this.cap = capacity;
    this.t = new Float64Array(capacity);
    this.v = new Float64Array(capacity);
    this.start = 0;
    this.length = 0;
  }

  clear() {
    this.start = 0;
    this.length = 0;
  }

  get lastT() {
    return this.length ? this.t[(this.start + this.length - 1) % this.cap] : -Infinity;
  }

  get firstT() {
    return this.length ? this.t[this.start] : Infinity;
  }

  /** Append; a sample older than the last one is dropped (callers feed in time order). Returns true if kept. */
  push(t, v) {
    if (this.length && t < this.lastT) return false;
    const val = typeof v === "number" ? v : NaN;
    if (this.length < this.cap) {
      const i = (this.start + this.length) % this.cap;
      this.t[i] = t;
      this.v[i] = val;
      this.length++;
    } else {
      this.t[this.start] = t;
      this.v[this.start] = val;
      this.start = (this.start + 1) % this.cap;
    }
    return true;
  }

  tAt(i) {
    return this.t[(this.start + i) % this.cap];
  }

  vAt(i) {
    return this.v[(this.start + i) % this.cap];
  }

  /** First logical index with t >= x (length if none). */
  lowerBound(x) {
    let lo = 0;
    let hi = this.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.tAt(mid) < x) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  /** Sample nearest to t within maxDt seconds, else null. */
  nearest(t, maxDt = Infinity) {
    if (!this.length) return null;
    const i = this.lowerBound(t);
    let best = -1;
    for (const j of [i - 1, i]) {
      if (j < 0 || j >= this.length) continue;
      if (best < 0 || Math.abs(this.tAt(j) - t) < Math.abs(this.tAt(best) - t)) best = j;
    }
    if (best < 0 || Math.abs(this.tAt(best) - t) > maxDt) return null;
    return { t: this.tAt(best), v: this.vAt(best) };
  }
}

/**
 * Points to draw for [t0, t1] on `columns` pixel columns. Up to 2 points per column (min and max in time order), so a
 * 100 Hz signal over 2 min (12000 samples) on 600 px draws at most 1200 points and spikes are never lost. One sample
 * on each side of the window is included so lines reach the edges. A gap (NaN, or a time gap above `gapS`) is a NaN
 * point, which the renderer turns into a break. Returns { t: number[], v: number[] }.
 */
export function decimate(rs, t0, t1, columns, gapS = 0.5) {
  const out = { t: [], v: [] };
  if (!rs.length || t1 <= t0 || columns < 1) return out;
  const i0 = Math.max(0, rs.lowerBound(t0) - 1);
  const i1 = Math.min(rs.length, rs.lowerBound(t1) + 1);
  const n = i1 - i0;
  if (n <= 0) return out;
  let lastT = null;
  const emit = (t, v) => {
    if (lastT !== null && t - lastT > gapS) {
      out.t.push(lastT);
      out.v.push(NaN);
    }
    out.t.push(t);
    out.v.push(v);
    lastT = t;
  };
  if (n <= columns * 2) {
    for (let i = i0; i < i1; i++) emit(rs.tAt(i), rs.vAt(i));
    return out;
  }
  const dt = (t1 - t0) / columns;
  let i = i0;
  while (i < i1) {
    const col = Math.floor((rs.tAt(i) - t0) / dt);
    let minI = -1;
    let maxI = -1;
    let nan = false;
    let j = i;
    for (; j < i1 && Math.floor((rs.tAt(j) - t0) / dt) === col; j++) {
      const v = rs.vAt(j);
      if (Number.isNaN(v)) {
        nan = true;
        continue;
      }
      if (minI < 0 || v < rs.vAt(minI)) minI = j;
      if (maxI < 0 || v > rs.vAt(maxI)) maxI = j;
    }
    if (minI >= 0) {
      const [a, b] = minI <= maxI ? [minI, maxI] : [maxI, minI];
      emit(rs.tAt(a), rs.vAt(a));
      if (b !== a) emit(rs.tAt(b), rs.vAt(b));
    }
    if (nan) emit(rs.tAt(j - 1), NaN);
    i = j;
  }
  return out;
}

/** Finite min and max of the decimated values of several series; null if there is none. */
export function valueRange(lists) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const vs of lists) {
    for (const v of vs) {
      if (!Number.isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  return lo === Infinity ? null : [lo, hi];
}

/** Padded y range; a flat signal gets a symmetric span so it is drawn in the middle. */
export function paddedRange(r, pad = 0.08) {
  if (!r) return [-1, 1];
  let [lo, hi] = r;
  if (hi - lo < 1e-12) {
    const s = Math.max(Math.abs(lo) * 0.1, 1e-3);
    return [lo - s, hi + s];
  }
  const p = (hi - lo) * pad;
  return [lo - p, hi + p];
}

/** "Nice" tick values (1, 2, 5 times a power of ten) covering [lo, hi] with about n ticks. */
export function niceTicks(lo, hi, n = 5) {
  if (!(hi > lo) || !Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  const raw = (hi - lo) / Math.max(1, n);
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) || 10 * p;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

/** Digits to print tick labels of a given step without noise. */
export function tickDigits(ticks) {
  if (ticks.length < 2) return 2;
  const step = Math.abs(ticks[1] - ticks[0]);
  return Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
}

/**
 * Visible time window. Following (live or replay playing): ends at `now`. Paused: ends at `endT` (scroll-back), never
 * beyond `now`. windowS = Infinity ("all") spans from the oldest sample `firstT`.
 */
export function viewWindow({ now, windowS, follow, endT, firstT }) {
  const end = follow || endT == null ? now : Math.min(endT, now);
  if (!Number.isFinite(windowS)) {
    const start = Number.isFinite(firstT) ? Math.min(firstT, end - 1) : end - 10;
    return [start, end];
  }
  return [end - windowS, end];
}

/** CSV of several series on their own time stamps (one row per sample of any series, empty cells elsewhere). */
export function toCsv(columns, t0, t1) {
  // columns: [{ name, rs }]
  const rows = new Map();
  columns.forEach((c, k) => {
    const i0 = c.rs.lowerBound(t0);
    for (let i = i0; i < c.rs.length && c.rs.tAt(i) <= t1; i++) {
      const t = c.rs.tAt(i);
      let r = rows.get(t);
      if (!r) rows.set(t, (r = new Array(columns.length).fill("")));
      const v = c.rs.vAt(i);
      r[k] = Number.isNaN(v) ? "NaN" : String(v);
    }
  });
  const esc = (s) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const lines = [["t_s", ...columns.map((c) => esc(c.name))].join(",")];
  for (const t of [...rows.keys()].sort((a, b) => a - b)) lines.push([t.toFixed(6), ...rows.get(t)].join(","));
  return lines.join("\n") + "\n";
}
