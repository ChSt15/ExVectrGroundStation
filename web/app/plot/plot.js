// Small canvas time-series plot for the tuning graphs (no dependency). One y axis per graph, min/max decimation per
// pixel column (series.js), shared time cursor, left drag = selection box zoom (Shift time only, Ctrl y only), right drag =
// pan, wheel / + / - = zoom, double click = reset, Backspace / Escape = back. The math is in zoom.js. Colours come from CSS tokens
// (--s1..--s8, --grid, --axis, --dim, --fg) so both themes work.
import { MIN_DRAG_PX } from "./zoom.js";
import { decimate, niceTicks, paddedRange, tickDigits, valueRange } from "./series.js";

const PAD = { l: 50, r: 10, t: 6, b: 20 };

const css = (el, name, fb) => getComputedStyle(el).getPropertyValue(name).trim() || fb;

export function seriesColor(el, slot) {
  return css(el, `--s${(slot % 8) + 1}`, "#3987e5");
}

export function createPlot(container, { onCursor, onPan, onZoom, onBox, onReset, onBack } = {}) {
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
    const xt = args.xTicks || niceTicks(args.t0, args.t1, Math.max(2, Math.floor((w - PAD.l - PAD.r) / 90))).map((t) => ({ t, label: args.xLabel ? args.xLabel(t) : t.toFixed(0) }));
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const { t, label } of xt) {
      const x = Math.round(xOf(t, args)) + 0.5;
      ctx.strokeStyle = grid;
      ctx.beginPath();
      ctx.moveTo(x, PAD.t);
      ctx.lineTo(x, hgt - PAD.b);
      ctx.stroke();
      ctx.fillStyle = dim;
      ctx.fillText(label, x, hgt - PAD.b + 4);
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
    if (args.xTitle) {
      ctx.textAlign = "right";
      ctx.textBaseline = "top";
      ctx.fillStyle = dim;
      ctx.globalAlpha = 0.8;
      ctx.fillText(args.xTitle, w - PAD.r - 2, PAD.t + 2);
      ctx.globalAlpha = 1;
    }
    // selection box
    if (sel) drawSel(ctx, axis);
    // frame
    ctx.strokeStyle = axis;
    ctx.strokeRect(PAD.l + 0.5, PAD.t + 0.5, w - PAD.l - PAD.r - 1, hgt - PAD.t - PAD.b - 1);
    last = { ...args, yRange: yr };
    return { yRange: yr, points };
  }

  const rect = () => ({ left: PAD.l, top: PAD.t, width: w - PAD.l - PAD.r, height: hgt - PAD.t - PAD.b });
  const modeOf = (e) => (e.shiftKey ? "time" : e.ctrlKey ? "y" : "both");

  function drawSel(ctx, axis) {
    const rc = rect();
    const cl = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    let x0 = cl(sel.x0, rc.left, rc.left + rc.width);
    let x1 = cl(sel.x1, rc.left, rc.left + rc.width);
    let y0 = cl(sel.y0, rc.top, rc.top + rc.height);
    let y1 = cl(sel.y1, rc.top, rc.top + rc.height);
    if (sel.mode === "time") [y0, y1] = [rc.top, rc.top + rc.height];
    if (sel.mode === "y") [x0, x1] = [rc.left, rc.left + rc.width];
    const small = Math.abs(sel.x1 - sel.x0) < MIN_DRAG_PX && Math.abs(sel.y1 - sel.y0) < MIN_DRAG_PX;
    if (small) return; // not a zoom yet
    ctx.save();
    ctx.fillStyle = css(container, "--s1", "#3987e5");
    ctx.globalAlpha = 0.18;
    ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = css(container, "--s1", axis);
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(Math.min(x0, x1) + 0.5, Math.min(y0, y1) + 0.5, Math.abs(x1 - x0), Math.abs(y1 - y0));
    ctx.restore();
  }

  // interaction: hover = shared cursor; left drag = selection box zoom; right drag = pan; wheel = zoom (factor);
  // double click = reset; Backspace / Escape = back one zoom step (Escape first cancels a selection in progress)
  let sel = null; // { x0, y0, x1, y1, mode }
  let pan = null; // { x, y }
  const redraw = () => last && draw(last);
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("pointermove", (e) => {
    if (!last) return;
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    if (sel) {
      sel.x1 = x;
      sel.y1 = y;
      sel.mode = modeOf(e);
      redraw();
      return;
    }
    if (pan) {
      const rc = rect();
      const dt = ((pan.x - x) / rc.width) * (last.t1 - last.t0);
      const dv = ((y - pan.y) / rc.height) * (last.yRange[1] - last.yRange[0]);
      pan.x = x;
      pan.y = y;
      onPan?.(dt, dv);
      return;
    }
    onCursor?.(x >= PAD.l && x <= w - PAD.r ? tOf(x, last) : null);
  });
  canvas.addEventListener("pointerleave", () => {
    if (!sel && !pan) onCursor?.(null);
  });
  canvas.addEventListener("pointerdown", (e) => {
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    if (e.button === 0) sel = { x0: x, y0: y, x1: x, y1: y, mode: modeOf(e) };
    else if (e.button === 2) pan = { x, y };
    else return;
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch (err) {
      /* synthetic events */
    }
  });
  canvas.addEventListener("pointerup", (e) => {
    if (sel && e.button === 0 && last) {
      const s = sel;
      sel = null;
      s.mode = modeOf(e);
      onBox?.({ x0: s.x0, y0: s.y0, x1: s.x1, y1: s.y1 }, s.mode, { rect: rect(), view: { t0: last.t0, t1: last.t1, y0: last.yRange[0], y1: last.yRange[1] } });
      redraw();
    }
    if (e.button === 2) pan = null;
  });
  canvas.addEventListener("pointercancel", () => {
    sel = null;
    pan = null;
    redraw();
  });
  canvas.addEventListener("dblclick", (e) => {
    e.preventDefault();
    sel = null;
    onReset?.();
  });
  canvas.addEventListener(
    "wheel",
    (e) => {
      if (!onZoom) return;
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      const rc = rect();
      const fx = Math.max(0, Math.min(1, (e.clientX - r.left - rc.left) / rc.width));
      const fy = Math.max(0, Math.min(1, 1 - (e.clientY - r.top - rc.top) / rc.height));
      onZoom(e.deltaY > 0 ? 1.25 : 0.8, { fx, fy, mode: modeOf(e) });
    },
    { passive: false },
  );
  canvas.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") onPan?.(-(last ? (last.t1 - last.t0) / 10 : 1));
    else if (e.key === "ArrowRight") onPan?.(last ? (last.t1 - last.t0) / 10 : 1);
    else if (e.key === "+" || e.key === "=") onZoom?.(0.8, null);
    else if (e.key === "-") onZoom?.(1.25, null);
    else if (e.key === "Backspace" || e.key === "Escape") {
      if (sel) {
        sel = null;
        redraw();
      } else onBack?.();
    } else return;
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
