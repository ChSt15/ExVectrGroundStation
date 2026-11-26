// Small canvas time-series plot for the tuning graphs (no dependency). A left and an optional right y axis (each series
// on one of them, axes.js), min/max decimation per pixel column (series.js), shared time cursor, left drag = pan,
// right drag = selection box zoom (Shift time only, Ctrl y only), wheel / + / - = zoom, double click = reset,
// Backspace / Escape = back. In replay (`replay: true` args) the cursor is the replay position bar: a click seeks, a
// drag on the bar scrubs (onSeek). The math is in zoom.js, axes.js and replayview.js. Colours come from CSS tokens
// (--s1..--s8, --grid, --axis, --dim, --fg) so both themes work.
import { MIN_DRAG_PX, dragAction } from "./zoom.js";
import { axisRange, hasRight, padsFor, sideOf } from "./axes.js";
import { decimate, niceTicks, tickDigits } from "./series.js";
import { nearBar } from "./replayview.js";

const PAD = { l: 50, r: 10, t: 6, b: 20 };

const css = (el, name, fb) => getComputedStyle(el).getPropertyValue(name).trim() || fb;

export function seriesColor(el, slot) {
  return css(el, `--s${(slot % 8) + 1}`, "#3987e5");
}

export function createPlot(container, { onCursor, onPan, onZoom, onBox, onReset, onBack, onSeek } = {}) {
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

  let pad = { ...PAD }; // l / r change with a right axis
  const xOf = (t, a) => pad.l + ((t - a.t0) / (a.t1 - a.t0)) * (w - pad.l - pad.r);
  const tOf = (x, a) => a.t0 + ((x - pad.l) / (w - pad.l - pad.r)) * (a.t1 - a.t0);

  function draw(args) {
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hgt);
    const dual = hasRight(args.series);
    pad = { ...PAD, ...padsFor(dual) };
    const cols = Math.max(10, Math.floor(w - pad.l - pad.r));
    const dec = args.series.map((s) => decimate(s.rs, args.t0, args.t1, cols));
    const listsOf = (side) => dec.filter((_, k) => sideOf(args.series[k]) === side).map((d) => d.v);
    const yr = axisRange(args.yRange, listsOf("L"));
    const yrR = dual ? axisRange(args.yRangeR, listsOf("R")) : null;
    const plotH = hgt - PAD.t - PAD.b;
    const mapper = (r) => (v) => PAD.t + (1 - (v - r[0]) / (r[1] - r[0])) * plotH;
    const yOf = mapper(yr);
    const yOfR = yrR ? mapper(yrR) : null;
    const grid = css(container, "--grid", "rgba(128,128,128,.18)");
    const axis = css(container, "--axis", "rgba(128,128,128,.5)");
    const dim = css(container, "--dim", "#8b98a8");
    const fg = css(container, "--fg", "#e6edf3");
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
    ctx.lineWidth = 1;
    const axisInk = (side) => args.axes?.[side]?.color || dim;
    // left y grid and labels
    const yt = niceTicks(yr[0], yr[1], Math.max(2, Math.floor(plotH / 34)));
    const yd = tickDigits(yt);
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const v of yt) {
      const y = Math.round(yOf(v)) + 0.5;
      ctx.strokeStyle = v === 0 ? axis : grid;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(w - pad.r, y);
      ctx.stroke();
      ctx.fillStyle = dual ? axisInk("L") : dim;
      ctx.fillText(v.toFixed(yd), pad.l - 6, y);
    }
    // right y axis: own ticks and labels (no grid lines, the left axis owns the grid), coloured like its series
    if (yrR) {
      const rt = niceTicks(yrR[0], yrR[1], Math.max(2, Math.floor(plotH / 34)));
      const rd = tickDigits(rt);
      ctx.textAlign = "left";
      for (const v of rt) {
        const y = Math.round(yOfR(v)) + 0.5;
        ctx.strokeStyle = axisInk("R");
        ctx.beginPath();
        ctx.moveTo(w - pad.r, y);
        ctx.lineTo(w - pad.r + 4, y);
        ctx.stroke();
        ctx.fillStyle = axisInk("R");
        ctx.fillText(v.toFixed(rd), w - pad.r + 6, y);
      }
    }
    // axis titles (rotated) when there are two axes
    if (dual) {
      for (const side of ["L", "R"]) {
        const label = args.axes?.[side]?.label;
        if (!label) continue;
        ctx.save();
        ctx.translate(side === "L" ? 9 : w - 9, PAD.t + plotH / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = axisInk(side);
        ctx.fillText(label, 0, 0, plotH);
        ctx.restore();
      }
    }
    // x grid and labels
    const xt = args.xTicks || niceTicks(args.t0, args.t1, Math.max(2, Math.floor((w - pad.l - pad.r) / 90))).map((t) => ({ t, label: args.xLabel ? args.xLabel(t) : t.toFixed(0) }));
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
    ctx.rect(pad.l, PAD.t, w - pad.l - pad.r, plotH);
    ctx.clip();
    let points = 0;
    args.series.forEach((s, k) => {
      const d = dec[k];
      const yMap = sideOf(s) === "R" && yOfR ? yOfR : yOf;
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
        const y = yMap(v);
        if (pen) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
        pen = true;
        points++;
      }
      ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.restore();
    // cursors: hover (live) / replay position bar (replay, thick) plus a thin dashed hover cursor
    let barX = null;
    const vline = (t, width, alpha, dash) => {
      if (t == null || t < args.t0 || t > args.t1) return null;
      const x = Math.round(xOf(t, args)) + 0.5;
      ctx.strokeStyle = fg;
      ctx.lineWidth = width;
      ctx.globalAlpha = alpha;
      ctx.setLineDash(dash);
      ctx.beginPath();
      ctx.moveTo(x, PAD.t);
      ctx.lineTo(x, hgt - PAD.b);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1;
      return x;
    };
    if (args.replay) {
      vline(args.hoverT, 1, 0.35, [2, 3]);
      barX = vline(args.cursorT, 2, 0.9, []);
      if (barX !== null) {
        ctx.fillStyle = fg; // grab handle at the top
        ctx.fillRect(barX - 4, PAD.t, 8, 4);
      }
    } else vline(args.cursorT, 1, 0.55, []);
    if (args.xTitle) {
      ctx.textAlign = "right";
      ctx.textBaseline = "top";
      ctx.fillStyle = dim;
      ctx.globalAlpha = 0.8;
      ctx.fillText(args.xTitle, w - pad.r - 2, PAD.t + 2);
      ctx.globalAlpha = 1;
    }
    // selection box
    if (sel) drawSel(ctx, axis);
    // frame
    ctx.strokeStyle = axis;
    ctx.strokeRect(pad.l + 0.5, PAD.t + 0.5, w - pad.l - pad.r - 1, plotH - 1);
    last = { ...args, yRange: yr, yRangeR: yrR, barX };
    return { yRange: yr, yRangeR: yrR, points };
  }

  const rect = () => ({ left: pad.l, top: PAD.t, width: w - pad.l - pad.r, height: hgt - PAD.t - PAD.b });
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

  // interaction (owner 2026-11-26): hover = shared cursor; left drag = pan (replay: a click seeks, a drag on the bar
  // scrubs); right drag = selection box zoom; wheel = zoom (factor); double click = reset; Backspace / Escape = back
  // one zoom step (Escape first cancels a selection in progress)
  let sel = null; // { x0, y0, x1, y1, mode }
  let pan = null; // { x, y, x0, y0, moved }
  let scrub = false;
  const redraw = () => last && draw(last);
  const inPlotX = (x) => x >= pad.l && x <= w - pad.r;
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
    if (scrub) {
      onSeek?.(tOf(Math.max(pad.l, Math.min(w - pad.r, x)), last), false);
      return;
    }
    if (pan) {
      const rc = rect();
      if (Math.abs(x - pan.x0) >= MIN_DRAG_PX || Math.abs(y - pan.y0) >= MIN_DRAG_PX) pan.moved = true;
      if (!pan.moved) return;
      const dt = ((pan.x - x) / rc.width) * (last.t1 - last.t0);
      const dv = ((y - pan.y) / rc.height) * (last.yRange[1] - last.yRange[0]);
      const dvR = last.yRangeR ? ((y - pan.y) / rc.height) * (last.yRangeR[1] - last.yRangeR[0]) : 0;
      pan.x = x;
      pan.y = y;
      onPan?.(dt, dv, dvR);
      return;
    }
    canvas.style.cursor = last.replay && nearBar(x, last.barX) ? "col-resize" : "";
    onCursor?.(inPlotX(x) ? tOf(x, last) : null);
  });
  canvas.addEventListener("pointerleave", () => {
    if (!sel && !pan && !scrub) onCursor?.(null);
  });
  canvas.addEventListener("pointerdown", (e) => {
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    const act = dragAction(e.button);
    if (act === "box") sel = { x0: x, y0: y, x1: x, y1: y, mode: modeOf(e) };
    else if (act === "pan") {
      if (last?.replay && onSeek && nearBar(x, last.barX)) scrub = true;
      else pan = { x, y, x0: x, y0: y, moved: false };
    } else return;
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch (err) {
      /* synthetic events */
    }
  });
  canvas.addEventListener("pointerup", (e) => {
    const act = dragAction(e.button);
    if (sel && act === "box" && last) {
      const s = sel;
      sel = null;
      s.mode = modeOf(e);
      const view = { t0: last.t0, t1: last.t1, y0: last.yRange[0], y1: last.yRange[1] };
      if (last.yRangeR) Object.assign(view, { r0: last.yRangeR[0], r1: last.yRangeR[1] });
      onBox?.({ x0: s.x0, y0: s.y0, x1: s.x1, y1: s.y1 }, s.mode, { rect: rect(), view });
      redraw();
    }
    if (act === "pan") {
      if (scrub) {
        const r = canvas.getBoundingClientRect();
        onSeek?.(tOf(Math.max(pad.l, Math.min(w - pad.r, e.clientX - r.left)), last), true);
      } else if (pan && !pan.moved && last?.replay && onSeek) {
        const r = canvas.getBoundingClientRect();
        const x = e.clientX - r.left;
        if (inPlotX(x)) onSeek(tOf(x, last), true); // a click on a graph moves the replay position
      }
      scrub = false;
      pan = null;
    }
  });
  canvas.addEventListener("pointercancel", () => {
    sel = null;
    pan = null;
    scrub = false;
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
