// Cache rules of the plot's series layer (web/app/plot/plot.js): the grid, axes and series are drawn to an offscreen
// canvas and only the cursor / bar overlay is redrawn per animation frame. The layer is invalid when its key changes.
// No DOM, unit tested in web/test/layercache.test.js.

/** Key of everything that changes the series layer: size, window, axes, data versions, colours (theme). */
export function layerKey({ w, h, dpr, t0, t1, yRange, yRangeR, series, axes, xTicks, xTitle, colors }) {
  return JSON.stringify([
    w, h, dpr, t0, t1, yRange ?? null, yRangeR ?? null,
    series.map((s) => [s.id ?? null, s.slot, s.dash ? 1 : 0, s.axis ?? "", s.rs ? s.rs.length : 0, s.rs ? s.rs.lastT : 0, s.rs ? s.rs.firstT : 0]),
    axes ? [axes.L?.label, axes.L?.color, axes.R?.label, axes.R?.color] : null,
    xTicks ? xTicks.map((x) => `${x.t}|${x.label}`) : null,
    xTitle ?? null,
    colors,
  ]);
}

/** One cached layer: `valid(key)` is true when the cache holds that key, `store(key, value)` replaces it. */
export class LayerCache {
  constructor() {
    this.key = null;
    this.value = null;
  }
  valid(key) {
    return this.key !== null && this.key === key;
  }
  store(key, value) {
    this.key = key;
    this.value = value;
  }
  invalidate() {
    this.key = null;
  }
}
