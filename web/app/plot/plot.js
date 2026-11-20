// Small canvas time-series plot for the tuning graphs (no dependency). One y axis per graph, min/max decimation per
// pixel column (series.js), shared time cursor, pan by dragging, zoom with the wheel. Colours come from CSS tokens
// (--s1..--s8, --grid, --axis, --dim, --fg) so both themes work.
import { decimate, niceTicks, paddedRange, tickDigits, valueRange } from "./series.js";

const PAD = { l: 50, r: 10, t: 6, b: 20 };

const css = (el, name, fb) => getComputedStyle(el).getPropertyValue(name).trim() || fb;

export function seriesColor(el, slot) {
  return css(el, `--s${(slot % 8) + 1}`, "#3987e5");
}

export function createPlot(container, { onCursor, onPan, onZoom } = {}) {
  const canvas = document.createElement("canvas");
  canvas.className = "plot-canvas";
  canvas.tabIndex = 0;
  container.append(canvas);
  let w = 0;
  let hgt = 0;
  let dpr = 1;
  let last = null; // last draw args, for hit testing and the PNG export
  const ro = new ResizeObserver(() => resize());
  ro.observe(container);

  function resize() {
    const r = container.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    w = Math.max(50, Math.floor(r.width));
    hgt = Math.max(60, Math.floor(r.height));
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(hgt * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${hgt}px`;
  }
  resize();

  const xOf = (t, a) => PAD.l + ((t - a.t0) / (a.t1 - a.t0)) * (w - PAD.l - PAD.r);
  const tOf = (x, a) => a.t0 + ((x - PAD.l) / (w - PAD.l - PAD.r)) * (a.t1 - a.t0);

  /**
   * args: { t0, t1, series: [{ rs, slot, dash }], yRange: [lo, hi] | null (auto), cursorT, xLabel(t) }.
   * Returns { yRange used, points drawn }.
   */
  function draw(args) {
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hgt);
    const cols = Math.max(10, Math.floor(w - PAD.l - PAD.r));
    const dec = args.series.map((s) => decimate(s.rs, args.t0, args.t1, cols));
    const yr = args.yRange || paddedRange(valueRange(dec.map((d) => d.v)));
    const [y0, y1] = yr;
    const yOf = (v) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (hgt - PAD.t - PAD.b);
    const grid = css(container, "--grid", "rgba(128,128,128,.18)");
    const axis = css(container, "--axis", "rgba(128,128,128,.5)");
    const dim = css(container, "--dim", "#8b98a8");
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
    ctx.lineWidth = 1;
    // y grid and labels
    const yt = niceTicks(y0, y1, Math.max(2, Math.floor((hgt - PAD.t - PAD.b) / 34)));
    const yd = tickDigits(yt);
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const v of yt) {
      const y = Math.round(yOf(v)) + 0.5;
      ctx.strokeStyle = v === 0 ? axis : grid;
      ctx.beginPath();
      ctx.moveTo(PAD.l, y);
      ctx.lineTo(w - PAD.r, y);
      ctx.stroke();
      ctx.fillStyle = dim;
      ctx.fillText(v.toFixed(yd), PAD.l - 6, y);
    }
    // x grid and labels
    const xt = niceTicks(args.t0, args.t1, Math.max(2, Math.floor((w - PAD.l - PAD.r) / 90)));
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const t of xt) {
      const x = Math.round(xOf(t, args)) + 0.5;
      ctx.strokeStyle = grid;
      ctx.beginPath();
      ctx.moveTo(x, PAD.t);
      ctx.lineTo(x, hgt - PAD.b);
      ctx.stroke();
      ctx.fillStyle = dim;
      ctx.fillText(args.xLabel ? args.xLabel(t) : t.toFixed(0), x, hgt - PAD.b + 4);
    }
    // series (clipped to the plot area)
    ctx.save();
    ctx.beginPath();
    ctx.rect(PAD.l, PAD.t, w - PAD.l - PAD.r, hgt - PAD.t - PAD.b);
    ctx.clip();
    let points = 0;
    args.series.forEach((s, k) => {
      const d = dec[k];
      ctx.strokeStyle = seriesColor(container, s.slot);
      ctx.lineWidth = 1.6;
      ctx.setLineDash(s.dash ? [6, 4] : []);
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < d.t.length; i++) {
        const v = d.v[i];
        if (!Number.isFinite(v)) {
          pen = false;
          continue;
        }
        const x = xOf(d.t[i], args);
        const y = yOf(v);
        if (pen) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
        pen = true;
        points++;
      }
      ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.restore();
    // cursor
    if (args.cursorT != null && args.cursorT >= args.t0 && args.cursorT <= args.t1) {
      const x = Math.round(xOf(args.cursorT, args)) + 0.5;
      ctx.strokeStyle = css(container, "--fg", "#e6edf3");
      ctx.globalAlpha = 0.55;
      ctx.beginPath();
      ctx.moveTo(x, PAD.t);
      ctx.lineTo(x, hgt - PAD.b);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    // frame
    ctx.strokeStyle = axis;
    ctx.strokeRect(PAD.l + 0.5, PAD.t + 0.5, w - PAD.l - PAD.r - 1, hgt - PAD.t - PAD.b - 1);
    last = { ...args, yRange: yr };
    return { yRange: yr, points };
  }

  // interaction: hover = shared cursor, drag = pan (seconds), wheel = zoom (factor)
  let drag = null;
  canvas.addEventListener("pointermove", (e) => {
    if (!last) return;
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    if (drag) {
      const dt = ((drag.x - x) / (w - PAD.l - PAD.r)) * (last.t1 - last.t0);
      drag.x = x;
      onPan?.(dt);
      return;
    }
    onCursor?.(x >= PAD.l && x <= w - PAD.r ? tOf(x, last) : null);
  });
  canvas.addEventListener("pointerleave", () => {
    if (!drag) onCursor?.(null);
  });
  canvas.addEventListener("pointerdown", (e) => {
    const r = canvas.getBoundingClientRect();
    drag = { x: e.clientX - r.left };
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch (err) {
      /* synthetic events */
    }
  });
  const end = () => (drag = null);
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener(
    "wheel",
    (e) => {
      if (!onZoom) return;
      e.preventDefault();
      onZoom(e.deltaY > 0 ? 1.25 : 0.8);
    },
    { passive: false },
  );
  canvas.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") onPan?.(-(last ? (last.t1 - last.t0) / 10 : 1));
    else if (e.key === "ArrowRight") onPan?.(last ? (last.t1 - last.t0) / 10 : 1);
    else if (e.key === "+" || e.key === "=") onZoom?.(0.8);
    else if (e.key === "-") onZoom?.(1.25);
    else return;
    e.preventDefault();
  });

  /** PNG with a title and legend line on top of the plot. */
  function toPng(title, legend) {
    const head = 40;
    const out = document.createElement("canvas");
    out.width = canvas.width;
    out.height = canvas.height + head * dpr;
    const c = out.getContext("2d");
    c.fillStyle = css(container, "--surface", "#151b23");
    c.fillRect(0, 0, out.width, out.height);
    c.scale(dpr, dpr);
    c.fillStyle = css(container, "--fg", "#e6edf3");
    c.font = "600 13px system-ui, sans-serif";
    c.fillText(title, 8, 16);
    c.font = "11px system-ui, sans-serif";
    let x = 8;
    for (const it of legend) {
      c.fillStyle = seriesColor(container, it.slot);
      c.fillRect(x, 26, 14, 3);
      c.fillStyle = css(container, "--fg", "#e6edf3");
      c.fillText(it.label, x + 18, 32);
      x += c.measureText(it.label).width + 34;
    }
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.drawImage(canvas, 0, head * dpr);
    return out.toDataURL("image/png");
  }

  return {
    canvas,
    draw,
    toPng,
    destroy() {
      ro.disconnect();
      canvas.remove();
    },
  };
}
