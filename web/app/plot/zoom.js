// Pure zoom / pan math of the tuning graphs (docs/gui.md "Tuning view", "Mouse"). No DOM, unit tested in
// web/test/zoom.test.js. Display only.

export const MIN_DRAG_PX = 4; // a smaller drag is a click, not a zoom
export const MIN_SPAN_S = 0.02; // smallest visible time span (below the 100 to 200 Hz sample period the raw samples show)

const minSpanY = (a, b) => Math.max(1e-9, (Math.abs(a) + Math.abs(b)) * 1e-9);

/** Order a pair and enforce a minimum span about its centre. */
function ordered(a, b, minSpan) {
  let lo = Math.min(a, b);
  let hi = Math.max(a, b);
  if (hi - lo < minSpan) {
    const m = (lo + hi) / 2;
    lo = m - minSpan / 2;
    hi = m + minSpan / 2;
  }
  return [lo, hi];
}

/**
 * Time range of a zoom, clamped to the loaded data [minT, maxT] and at least `minSpan` wide. Returns null if the range
 * lies completely outside the data.
 */
export function zoomTimeRange(a, b, minT, maxT, minSpan = MIN_SPAN_S) {
  let [lo, hi] = ordered(a, b, minSpan);
  if (Number.isFinite(minT) && lo < minT) lo = minT;
  if (Number.isFinite(maxT) && hi > maxT) hi = maxT;
  if (!(hi > lo)) return null;
  if (hi - lo < minSpan) {
    if (Number.isFinite(maxT) && lo + minSpan > maxT) lo = hi - minSpan;
    else hi = lo + minSpan;
  }
  return [lo, hi];
}

/** Y range of a zoom, at least a tiny relative span wide. */
export function zoomYRange(a, b) {
  const [lo, hi] = ordered(a, b, 0);
  const m = minSpanY(lo, hi);
  return hi - lo < m ? ordered(lo, hi, m) : [lo, hi];
}

/**
 * Selection box (pixels, canvas coordinates) to axis ranges. rect = { left, top, width, height } of the plot area,
 * view = { t0, t1, y0, y1 } of the drawn axes. mode: "both" (default), "time" (Shift) or "y" (Ctrl). In "both" an axis
 * whose drag is below `minPx` is left alone (a thin box zooms the other axis only). Returns { t: [lo, hi] | null,
 * y: [lo, hi] | null } or null when nothing should zoom.
 */
export function boxToRanges(box, rect, view, { mode = "both", minPx = MIN_DRAG_PX, minT = -Infinity, maxT = Infinity, minSpan = MIN_SPAN_S } = {}) {
  const cl = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const xa = cl(box.x0, rect.left, rect.left + rect.width);
  const xb = cl(box.x1, rect.left, rect.left + rect.width);
  const ya = cl(box.y0, rect.top, rect.top + rect.height);
  const yb = cl(box.y1, rect.top, rect.top + rect.height);
  const dx = Math.abs(xb - xa);
  const dy = Math.abs(yb - ya);
  const doT = mode !== "y" && dx >= minPx;
  const doY = mode !== "time" && dy >= minPx;
  if (!doT && !doY) return null;
  const tOf = (x) => view.t0 + ((x - rect.left) / rect.width) * (view.t1 - view.t0);
  const vOf = (y) => view.y0 + (1 - (y - rect.top) / rect.height) * (view.y1 - view.y0);
  let t = null;
  let y = null;
  if (doT) {
    t = zoomTimeRange(tOf(xa), tOf(xb), minT, maxT, minSpan);
    if (!t && !doY) return null;
  }
  if (doY) y = zoomYRange(vOf(ya), vOf(yb));
  return t || y ? { t, y } : null;
}

/**
 * Slide the time window [t0, t1] by dt seconds without changing its span. Never beyond `maxT` (now) on the right; on
 * the left not past the oldest data `minT` (a window that already starts before it cannot go further back).
 */
export function panWindow(t0, t1, dt, minT = -Infinity, maxT = Infinity) {
  let shift = dt;
  if (dt < 0) shift = Math.max(dt, Math.min(t0, minT) - t0);
  else shift = Math.min(dt, Math.max(0, maxT - t1));
  return [t0 + shift, t1 + shift];
}

/** Slide a y range by dv value units. */
export function panY(lo, hi, dv) {
  return [lo + dv, hi + dv];
}

/** Zoom a time span by factor f (wheel, + / -) keeping the right edge, within [minSpan, maxSpan]. */
export function scaleSpan(span, f, minSpan = MIN_SPAN_S, maxSpan = Infinity) {
  return Math.max(minSpan, Math.min(maxSpan, span * f));
}

/** Undo stack of zoom steps (snapshots are opaque to it). */
export class ZoomStack {
  constructor(limit = 50) {
    this.limit = limit;
    this.items = [];
  }

  get length() {
    return this.items.length;
  }

  push(snap) {
    this.items.push(snap);
    if (this.items.length > this.limit) this.items.shift();
  }

  pop() {
    return this.items.pop() ?? null;
  }

  clear() {
    this.items = [];
  }
}

/**
 * Wheel zoom about a point: the value at fraction `f0` (0 = lo, 1 = hi) of [lo, hi] stays where it is while the span
 * is multiplied by `factor` (> 1 zooms out), within [minSpan, maxSpan]. `max` (optional) caps the upper end by sliding
 * the range back (the time axis never goes beyond now).
 */
export function zoomAbout(lo, hi, f0, factor, { minSpan = MIN_SPAN_S, maxSpan = Infinity, max = Infinity } = {}) {
  const span = hi - lo;
  const n = Math.max(minSpan, Math.min(maxSpan, span * factor));
  const anchor = lo + f0 * span;
  let a = anchor - f0 * n;
  let b = a + n;
  if (b > max) {
    b = max;
    a = b - n;
  }
  return [a, b];
}
