// Tuning view: graph workspace (docs/gui.md "Tuning view"). Ready-made graphs from the signal catalogue with a shared
// time axis (mission time T0 as the vehicle reports it, relative time when there is no mission clock) and cursor,
// window 10 s / 30 s / 2 min / all, pause with scroll-back, mouse (left drag pan, right drag zoom box, wheel at the
// pointer, double click reset, zoom stack, web/app/plot/zoom.js), signal picker per graph with a left / right y axis
// per series, auto or manual y range per axis, legend with the value at the cursor, CSV and PNG export. Live: history
// comes from the core's plot buffer (get_history buffer "plot": a page reload keeps the last 2 min), then every packet
// is appended (subscription mode "all"). Replay: the whole recording is loaded once (get_history whole:true) and drawn
// at once, the replay position is a bar (web/app/plot/replayview.js). Vehicle-reported values only; derived signals
// are display-only.
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { fmtClock, fmtNum } from "../fmt.js";
import { RingSeries, toCsv, viewWindow } from "../plot/series.js";
import {
  DEFAULT_GRAPHS, GRAPH_PRESETS, HISTORY_FIELDS, MAX_SERIES, SIGNALS, SIGNAL_BY_ID, SIGNAL_TYPES, SignalFeeder, axisTitle, graphUnits,
  historyToPackets, presetGraph, sanitizeGraphs, setAxis, toggleSignal,
} from "../plot/catalogue.js";
import { createPlot, seriesColor } from "../plot/plot.js";
import { MissionClock, fmtT } from "../plot/missionclock.js";
import { missionAxis } from "../plot/missionclock.js";
import { ZoomStack, boxToRanges, MIN_SPAN_S, panWindow, panY, zoomAbout } from "../plot/zoom.js";
import { hasRight, manualRange, seriesOn, sideOf } from "../plot/axes.js";
import { REPLAY_MAX_POINTS, clampToLog, logKey, logRange, replayWindow } from "../plot/replayview.js";

const WINDOWS = [["10 s", 10], ["30 s", 30], ["2 min", 120], ["all", Infinity]];
const LIVE_HISTORY_S = 130; // the core keeps about this much (PLOT_WINDOW_S)
const RING = 16384; // > 130 s at 100 Hz
const RING_REPLAY = REPLAY_MAX_POINTS + 4096; // the whole recording, thinned by the core to REPLAY_MAX_POINTS
const FRAME_MS = 66; // about 15 full redraws per second
const LEGEND_MS = 50; // replay: legend values at the bar, at most 20 per second
const fmtV = (p, unit) => `${Number.isFinite(p.v) ? fmtNum(p.v, Math.abs(p.v) >= 100 ? 1 : Math.abs(p.v) >= 1 ? 2 : 3) : "NaN"} ${unit}`;
const STORE_KEY = "exgs.tuning.graphs";
const WIN_KEY = "exgs.tuning.window";

const lsGet = (k) => {
  try {
    return localStorage.getItem(k);
  } catch (e) {
    return null;
  }
};
const lsSet = (k, v) => {
  try {
    localStorage.setItem(k, v);
  } catch (e) {
    /* not remembered */
  }
};

function download(name, href) {
  const a = h("a", { href, download: name });
  document.body.append(a);
  a.click();
  a.remove();
}

registerPanel({
  id: "tuning",
  title: "Tuning graphs",
  consumes: SIGNAL_TYPES,
  mode: "all", // every sample, not the 20 Hz latest-value stream
  create(el, ctx) {
    let ringCap = RING;
    const feeder = new SignalFeeder(() => new RingSeries(ringCap));
    let graphs = sanitizeGraphs(JSON.parse(lsGet(STORE_KEY) || "null")) || DEFAULT_GRAPHS.map(presetGraph);
    // the window preset is remembered per mode: live default 30 s, replay default "all" (the whole recording)
    const winKey = (v) => (v === "replay" ? `${WIN_KEY}.replay` : WIN_KEY);
    const readWindow = (v) => {
      const raw = lsGet(winKey(v));
      if (raw === "all") return Infinity;
      const n = Number(raw);
      return WINDOWS.some(([, s]) => s === n) ? n : v === "replay" ? Infinity : 30;
    };
    let windowS = readWindow(ctx.view());
    let presetS = windowS; // the selected window preset, what a reset returns to
    let loadedKey = null; // replay: the recording whose whole log is loaded
    let hoverT = null;
    const mclock = new MissionClock();
    const zstack = new ZoomStack();
    let viewT = [0, 1]; // the time window drawn last
    let axisMode = "relative";
    let paused = false;
    let endT = null; // window end while paused
    let cursorT = null;
    let loading = false;
    let queue = [];
    let histGen = 0;
    let status = "";

    // ---------------------------------------------------------------- toolbar
    const winBtns = WINDOWS.map(([label, s]) => h("button", { text: label, "aria-pressed": String(s === windowS), onclick: () => setWindow(s) }));
    const pauseBtn = h("button", { class: "pause", text: "❚❚ Pause", title: "freeze the graphs (data keeps arriving); left-drag or arrow keys to scroll back", onclick: () => setPaused(!paused) });
    const addSel = h("select", { "aria-label": "add a graph", onchange: () => { if (addSel.value) addGraph(addSel.value); addSel.value = ""; } },
      h("option", { value: "", text: "+ Add graph…" }), ...GRAPH_PRESETS.map((g) => h("option", { value: g.id, text: g.title })), h("option", { value: "__empty", text: "Empty graph" }));
    const resetBtn = h("button", { text: "Reset graphs", title: "back to the ready-made set", onclick: () => { graphs = DEFAULT_GRAPHS.map(presetGraph); save(); rebuild(); } });
    const backBtn = h("button", { text: "Back", title: "undo the last zoom step (Backspace or Escape on a graph)", disabled: true, onclick: () => zoomBack() });
    const zoomResetBtn = h("button", { text: "Reset zoom", title: "all graphs: y back to auto, time window back to the selected preset, live (double click on a graph does this for that graph's y)", onclick: () => resetZoom(null) });
    const csvAll = h("button", { text: "CSV (all)", title: "every signal of every graph in the visible window", onclick: () => exportCsv(null) });
    const info = h("span", { class: "hint tn-info", "data-testid": "tuning-info" });
    const mouseHint = h("div", { class: "hint tn-mouse", "data-testid": "tuning-mouse", text: "Mouse: left drag = pan (replay: click or drag the bar = seek) · right drag = zoom box (Shift time only, Ctrl y only) · wheel = zoom at the pointer (Shift time only, Ctrl y only; y only on a manual-y axis) · double click = reset · Backspace / Esc = back" });
    el.append(h("div", { class: "toolbar tn-bar" }, h("span", { class: "seg", role: "group", "aria-label": "window" }, ...winBtns), pauseBtn, zoomResetBtn, backBtn, addSel, csvAll, resetBtn, info), mouseHint);
    const grid = h("div", { class: "tn-grid" });
    el.append(grid);

    const save = () => lsSet(STORE_KEY, JSON.stringify(graphs));
    function setWindow(s) {
      windowS = presetS = s;
      lsSet(winKey(ctx.view()), Number.isFinite(s) ? String(s) : "all");
      if (isReplay() && paused) setPaused(false); // a preset is the zoom level of the replay graphs
      syncWinBtns();
      if (!isReplay() && (!Number.isFinite(s) || s > 30)) loadHistory();
    }
    const syncWinBtns = () => winBtns.forEach((b, i) => b.setAttribute("aria-pressed", String(WINDOWS[i][1] === windowS && windowS === presetS)));
    const isReplay = () => ctx.view() === "replay";
    const replayRange = () => logRange(ctx.status()?.replay);
    const dataFirst = () => {
      if (isReplay()) return replayRange()?.[0] ?? -Infinity;
      let first = Infinity;
      for (const r of feeder.rings.values()) if (r.length && r.firstT < first) first = r.firstT;
      return first;
    };
    // newest time the window may reach (live: now, replay: the end of the log) and the widest span
    const timeMax = (now) => (isReplay() ? replayRange()?.[1] ?? now : now);
    const histSpan = () => (isReplay() ? (replayRange() ? replayRange()[1] - replayRange()[0] : Infinity) : LIVE_HISTORY_S);
    const axisState = (a) => ({ auto: a.auto.checked, lo: a.min.value, hi: a.max.value });
    const snapshot = () => ({ windowS, paused, endT, y: cards.map((c) => ({ ...axisState(c.ax.L), r: axisState(c.ax.R) })) });
    function setManualY(c, lo, hi, side = "L") {
      const a = c.ax[side];
      a.min.value = String(Number(lo.toPrecision(7)));
      a.max.value = String(Number(hi.toPrecision(7)));
      if (a.auto.checked) {
        a.auto.checked = false;
        a.auto.dispatchEvent(new Event("change"));
        a.min.value = String(Number(lo.toPrecision(7)));
        a.max.value = String(Number(hi.toPrecision(7)));
      }
    }
    function setAutoY(c, side = "L") {
      const a = c.ax[side];
      if (a.auto.checked) return;
      a.auto.checked = true;
      a.auto.dispatchEvent(new Event("change"));
    }
    // a time window that is frozen (paused) with the shown span, so pan / zoom steps work from what is on screen
    function freezeWindow(t0, t1) {
      if (!paused) setPaused(true);
      windowS = t1 - t0;
      endT = t1;
      syncWinBtns();
    }
    function zoomBack() {
      const sn = zstack.pop();
      if (!sn) return;
      windowS = sn.windowS;
      if (sn.paused) {
        if (!paused) setPaused(true);
        endT = sn.endT;
      } else if (paused) setPaused(false);
      if (sn.y.length === cards.length) {
        cards.forEach((c, i) => {
          const y = sn.y[i];
          if (y.auto) setAutoY(c);
          else setManualY(c, Number(y.lo), Number(y.hi));
          const r = y.r;
          if (r) {
            if (r.auto) setAutoY(c, "R");
            else setManualY(c, Number(r.lo), Number(r.hi), "R");
          }
        });
      }
      syncWinBtns();
    }
    /** card = a card (its y only) or null (all graphs); the time window always goes back to the preset, live. */
    function resetZoom(card) {
      zstack.push(snapshot());
      for (const c of card ? [card] : cards) {
        setAutoY(c);
        setAutoY(c, "R");
      }
      windowS = presetS;
      if (paused) setPaused(false);
      syncWinBtns();
    }
    function setPaused(p) {
      paused = p;
      endT = p ? ctx.now() : null;
      if (isReplay()) {
        // replay: freezing keeps the window on screen; unfreezing returns to the preset
        if (p) {
          if (!Number.isFinite(windowS)) windowS = viewT[1] - viewT[0];
          endT = viewT[1];
        } else windowS = presetS;
        syncWinBtns();
      }
      pauseBtn.textContent = p ? (isReplay() ? "▶ Follow" : "▶ Live") : isReplay() ? "❚❚ Freeze" : "❚❚ Pause";
      pauseBtn.setAttribute("aria-pressed", String(p));
    }
    function addGraph(id) {
      const g = id === "__empty" ? { preset: "", title: "Graph", note: "", series: [] } : presetGraph(id);
      if (g) graphs.push(g);
      save();
      rebuild();
    }

    // ---------------------------------------------------------------- graphs
    let cards = [];
    function rebuild() {
      for (const c of cards) c.plot.destroy();
      cards = [];
      zstack.clear();
      grid.replaceChildren();
      graphs.forEach((g, gi) => cards.push(makeCard(g, gi)));
    }

    function makeCard(g, gi) {
      const title = h("h3", { text: g.title });
      const unit = h("span", { class: "tn-unit" });
      // y range controls per axis: left ("auto y") and right ("auto y R", shown only when a series is on the right axis)
      const axisCtl = (side) => {
        const name = side === "L" ? "" : " right";
        const auto = h("input", { type: "checkbox", checked: true, "aria-label": `automatic${name} y range` });
        const min = h("input", { type: "number", step: "any", class: "tn-y", "aria-label": `${name} y min`.trim(), disabled: true, hidden: true });
        const max = h("input", { type: "number", step: "any", class: "tn-y", "aria-label": `${name} y max`.trim(), disabled: true, hidden: true });
        const wrap = h("span", { class: "tn-axisctl", hidden: side === "R" }, h("label", { class: "tn-auto" }, auto, side === "L" ? " auto y" : " auto y R"), min, max);
        const a = { auto, min, max, wrap, lastY: null };
        auto.addEventListener("change", () => {
          min.disabled = max.disabled = min.hidden = max.hidden = auto.checked;
          if (!auto.checked && a.lastY) {
            min.value = a.lastY[0].toPrecision(4);
            max.value = a.lastY[1].toPrecision(4);
          }
        });
        return a;
      };
      const ax = { L: axisCtl("L"), R: axisCtl("R") };
      const yAuto = ax.L.auto;
      const yMin = ax.L.min;
      const yMax = ax.L.max;
      const pickBtn = h("button", { class: "small", text: "Signals ▾", "aria-expanded": "false" });
      const csvBtn = h("button", { class: "small", text: "CSV", onclick: () => exportCsv(gi) });
      const pngBtn = h("button", { class: "small", text: "PNG", onclick: () => exportPng(gi) });
      const delBtn = h("button", { class: "small", text: "✕", title: "remove graph", "aria-label": "remove graph", onclick: () => { graphs.splice(gi, 1); save(); rebuild(); } });
      const head = h("div", { class: "tn-head" }, title, unit, h("span", { class: "grow" }), ax.L.wrap, ax.R.wrap, pickBtn, csvBtn, pngBtn, delBtn);
      const picker = h("div", { class: "tn-picker", hidden: true });
      const legend = h("div", { class: "tn-legend" });
      const plotBox = h("div", { class: "tn-plot" });
      const note = h("div", { class: "hint tn-note", text: g.note || "" });
      const card = h("section", { class: "tn-card", "data-graph": g.preset || `g${gi}` }, head, picker, plotBox, legend, note);
      grid.append(card);
      const plot = createPlot(plotBox, {
        onCursor: (t) => (cursorT = t),
        onSeek: (t, final) => seekTo(t, final),
        onPan: (dt, dv, dvR) => {
          const now = ctx.now();
          if (now === null) return;
          const [t0, t1] = panWindow(viewT[0], viewT[1], dt, dataFirst(), timeMax(now));
          freezeWindow(t0, t1);
          if (dv && !ax.L.auto.checked && ax.L.lastY) setManualY(c, ...panY(ax.L.lastY[0], ax.L.lastY[1], dv));
          if (dvR && !ax.R.auto.checked && ax.R.lastY) setManualY(c, ...panY(ax.R.lastY[0], ax.R.lastY[1], dvR), "R");
        },
        onZoom: (f, o) => {
          const now = ctx.now();
          if (now === null) return;
          const mode = o?.mode || "both";
          if (mode !== "y") {
            const fx = o ? o.fx : 1; // keys: about the right edge, as before
            const [t0, t1] = zoomAbout(viewT[0], viewT[1], fx, f, { maxSpan: histSpan(), max: timeMax(now) });
            if (fx >= 0.98 && !paused && !isReplay()) {
              windowS = t1 - t0; // anchored at the right edge: stay live
              syncWinBtns();
            } else freezeWindow(t0, t1);
          }
          if (o && mode !== "time") {
            for (const side of ["L", "R"]) {
              const a = ax[side];
              if (a.auto.checked || !a.lastY) continue;
              const [y0, y1] = a.lastY;
              const mid = Math.max(Math.abs(y0), Math.abs(y1));
              setManualY(c, ...zoomAbout(y0, y1, o.fy, f, { minSpan: Math.max(1e-12, mid * 1e-9) }), side);
            }
          }
        },
        onBox: (box, mode, geom) => {
          const now = ctx.now();
          if (now === null) return;
          const r = boxToRanges(box, geom.rect, geom.view, { mode, minT: dataFirst(), maxT: timeMax(now), minSpan: MIN_SPAN_S });
          if (!r) return; // a click or a tiny drag
          zstack.push(snapshot());
          if (r.t) freezeWindow(r.t[0], r.t[1]);
          if (r.y) setManualY(c, r.y[0], r.y[1]);
          if (r.yr) setManualY(c, r.yr[0], r.yr[1], "R");
        },
        onReset: () => resetZoom(c),
        onBack: () => zoomBack(),
      });
      pickBtn.addEventListener("click", () => {
        picker.hidden = !picker.hidden;
        pickBtn.setAttribute("aria-expanded", String(!picker.hidden));
        if (!picker.hidden) buildPicker();
      });
      function buildPicker() {
        const groups = new Map();
        for (const s of SIGNALS) {
          if (!groups.has(s.group)) groups.set(s.group, []);
          groups.get(s.group).push(s);
        }
        const used = new Set(graphs[gi].series.map((x) => x.sig));
        picker.replaceChildren(
          h("div", { class: "hint", text: `Up to ${MAX_SERIES} signals; grey = not received yet. L / R puts a signal on the left or right y axis: one unit per axis reads best.` }),
          ...[...groups].map(([name, list]) =>
            h("fieldset", {}, h("legend", { text: name }), ...list.map((s) => {
              const cb = h("input", { type: "checkbox", checked: used.has(s.id) });
              const side = () => sideOf(graphs[gi].series.find((x) => x.sig === s.id));
              const lr = h("button", { class: "small tn-ax", type: "button", "data-axis-toggle": s.id, hidden: !used.has(s.id), title: "left / right y axis" });
              const syncLr = () => {
                lr.hidden = !graphs[gi].series.some((x) => x.sig === s.id);
                if (!lr.hidden) lr.textContent = side();
              };
              syncLr();
              lr.addEventListener("click", (ev) => {
                ev.preventDefault();
                graphs[gi] = setAxis(graphs[gi], s.id, side() === "R" ? "L" : "R");
                save();
                c.refreshLegend();
                syncLr();
              });
              cb.addEventListener("change", () => {
                graphs[gi] = toggleSignal(graphs[gi], s.id);
                save();
                c.refreshLegend();
                cb.checked = graphs[gi].series.some((x) => x.sig === s.id);
                syncLr();
              });
              return h("label", { class: feeder.supplied.has(s.id) ? "" : "dim", title: s.desc || "" }, cb, ` ${s.label} [${s.unit}] `, lr);
            })),
          ),
        );
      }
      const c = {
        card, plot, legend, unit, ax, yAuto, yMin, yMax, items: [],
        refreshLegend() {
          const gr = graphs[gi];
          const dual = hasRight(gr.series);
          unit.textContent = dual ? `left ${graphUnits({ series: seriesOn(gr.series, "L") }).join(", ") || "-"} · right ${graphUnits({ series: seriesOn(gr.series, "R") }).join(", ")}` : graphUnits(gr).join(", ");
          ax.R.wrap.hidden = !dual;
          if (!dual) setAutoY(c, "R");
          c.items = gr.series.map((x) => {
            const s = SIGNAL_BY_ID.get(x.sig);
            const val = h("span", { class: "tn-val" });
            const hov = h("span", { class: "tn-hov" });
            const sw = h("span", { class: `sw s${(x.slot % 8) + 1}${x.dash ? " dash" : ""}` });
            const side = sideOf(x);
            const badge = dual ? h("span", { class: "tn-side", title: side === "R" ? "right y axis" : "left y axis", text: side }) : null;
            return { x, s, val, hov, el: h("span", { class: "tn-item", "data-axis": side, title: `${s.desc || ""}${dual ? ` (${side === "R" ? "right" : "left"} axis)` : ""}`.trim() }, sw, h("span", { text: s.label }), ...(badge ? [badge] : []), val, hov) };
          });
          legend.replaceChildren(...c.items.map((i) => i.el));
          if (!gr.series.length) legend.replaceChildren(h("span", { class: "hint", text: "no signals: use Signals ▾" }));
        },
      };
      c.refreshLegend();
      return c;
    }

    // ---------------------------------------------------------------- data
    function feed(type, t, f) {
      if (type === "MissionState") mclock.add(t, f.t_mission ?? null, f.mission_phase, f.mission_mode, f.mission_elapsed);
      feeder.push(type, t, f);
    }
    // replay: seek the replay (display only: moves the viewer). The bar shows the new position at once; the requests are
    // throttled and coalesced, the last one is sent at once when the drag ends (ctx.replaySeek, main.js).
    function seekTo(t, final) {
      const range = replayRange();
      if (!range || !isReplay()) return;
      cursorT = null;
      ctx.replaySeek(clampToLog(t, range), final);
    }
    async function loadHistory() {
      const gen = ++histGen;
      const view = ctx.view();
      const now = ctx.now();
      if (now === null || ctx.api.state !== "open") return;
      const replay = view === "replay";
      const rs = ctx.status()?.replay;
      const range = replay ? logRange(rs) : null;
      if (replay && !range) {
        status = "no replay log open";
        return;
      }
      loading = true;
      queue = [];
      ringCap = replay ? RING_REPLAY : RING;
      const series = Object.entries(HISTORY_FIELDS).map(([type, fields]) => ({ type, fields }));
      status = replay ? "loading the whole recording…" : "loading history…";
      // replay: the entire log once (thinned by the core to REPLAY_MAX_POINTS per series), not per seek
      const req = replay
        ? { view, buffer: "plot", series, t0: range[0] - 0.01, t1: range[1] + 0.01, max_points: REPLAY_MAX_POINTS, whole: true }
        : { view, buffer: "plot", series, t0: now - LIVE_HISTORY_S, t1: now + 1, max_points: 100000 };
      try {
        const res = await ctx.api.request("get_history", req, replay ? 120000 : 20000);
        if (gen !== histGen) return;
        feeder.rings.clear();
        feeder.clear();
        mclock.clear();
        const cal = ctx.store.get(view, "Calibration");
        if (cal) feed("Calibration", cal.t, cal.f);
        const home = ctx.store.get(view, "HomePosition"); // not in the plot history: the reference of the GNSS metres
        if (home) feed("HomePosition", home.t, home.f);
        for (const p of historyToPackets(res.series)) feed(p.type, p.t, p.f);
        if (!replay) for (const it of queue) feed(it.type, it.t, it.f);
        loadedKey = replay ? logKey(rs) : null;
        status = "";
      } catch (e) {
        if (gen === histGen) {
          status = `history: ${e.message}`;
          if (replay) loadedKey = logKey(rs); // do not retry on every frame
        }
      } finally {
        if (gen === histGen) {
          loading = false;
          queue = [];
        }
      }
    }

    function onPacket(it) {
      if (isReplay()) return; // replay: the whole recording is already loaded, the playing packets add nothing
      if (loading) queue.push(it);
      else feed(it.type, it.t, it.f);
    }

    // ---------------------------------------------------------------- render loop
    let lastFrame = 0;
    let raf = 0;
    let points = 0;
    let frameMs = 0;
    // replay: between the full frames (FRAME_MS) every animation frame only moves the bar (cached series layer, one blit
    // plus the overlay), the legend values at the bar follow at most every LEGEND_MS (docs/gui.md "Replay smoothness")
    let lastCur = null;
    let lastHov = null;
    let lastLegend = 0;
    let nearMaxNow = 0.5;
    function barFrame(ts) {
      const cur = ctx.replayPos();
      if (cur === null) return;
      if (cur !== lastCur || cursorT !== lastHov) {
        lastCur = cur;
        lastHov = cursorT;
        for (const c of cards) c.plot.setCursor(cur, cursorT);
      }
      if (ts - lastLegend >= LEGEND_MS) {
        lastLegend = ts;
        for (const c of cards) legendValues(c, cur, cursorT, nearMaxNow);
      }
    }
    function legendValues(c, cur, hov, nearMax) {
      for (const it of c.items) {
        const ring = feeder.ring(it.x.sig);
        const p = cur != null ? ring.nearest(cur, nearMax) : ring.length ? { t: ring.lastT, v: ring.vAt(ring.length - 1) } : null;
        it.val.textContent = p ? ` ${fmtV(p, it.s.unit)}` : feeder.supplied.has(it.x.sig) ? " —" : " not received";
        const ph = hov != null ? ring.nearest(hov, nearMax) : null;
        it.hov.textContent = ph ? ` @ ${fmtV(ph, it.s.unit)}` : "";
        it.el.dataset.missing = String(!feeder.supplied.has(it.x.sig));
      }
    }
    function frame(ts) {
      raf = requestAnimationFrame(frame);
      if (el.offsetParent === null || document.hidden) return;
      if (ts - lastFrame < FRAME_MS) {
        if (isReplay() && !loading) barFrame(ts);
        return;
      }
      lastFrame = ts;
      const t0p = performance.now();
      const now = ctx.now();
      if (now === null) return;
      const view = ctx.view();
      const rs = ctx.status()?.replay;
      const replay = view === "replay";
      const range = replay ? logRange(rs) : null;
      if (replay) {
        if (!range) return;
        if (!loading && loadedKey !== logKey(rs) && ctx.api.state === "open") loadHistory(); // a log opened or changed
      }
      const [t0, t1] = replay
        ? replayWindow({ range, pos: ctx.replayPos() ?? now, windowS, frozen: paused && endT != null ? [endT - windowS, endT] : null })
        : viewWindow({ now, windowS, follow: !paused, endT, firstT: dataFirst() });
      viewT = [t0, t1];
      const ax = missionAxis(mclock, t0, t1, Math.max(3, Math.floor(el.clientWidth / 140)));
      axisMode = ax.mode;
      const relLabel = replay && rs && rs.t_start != null ? (t) => fmtClock(t - rs.t_start) : (t) => `${fmtNum(t - t1, 0)} s`;
      const xLabel = ax.mode === "mission" ? ax.label : relLabel;
      const xTitle = ax.mode === "mission" ? ax.title : view === "replay" ? "log time" : "t (s, relative to now)";
      info.title = ax.mode === "mission" ? ax.tip : "x axis: no mission clock (no MissionState time) in the loaded data, so time is relative: " + (view === "replay" ? "log time" : "seconds before the window end");
      // live: the cursor is the hover (legends show the latest values without hover). Replay: the cursor is the
      // replay position bar (the legends show the values at the bar) and the hover is a second, thinner cursor whose
      // values are shown dimmed in the legends after "@".
      const barT = replay && rs && rs.t != null ? ctx.replayPos() ?? rs.t : null;
      const cur = replay ? barT : cursorT;
      const hov = replay ? cursorT : null;
      const nearMax = replay ? Math.max(0.5, (t1 - t0) / 400) : 0.5;
      nearMaxNow = nearMax;
      lastCur = cur;
      lastHov = hov;
      lastLegend = ts;
      points = 0;
      cards.forEach((c, gi) => {
        const g = graphs[gi];
        const dual = hasRight(g.series);
        const colorOf = (side) => {
          const slots = new Set(seriesOn(g.series, side).map((x) => x.slot));
          return slots.size === 1 ? seriesColor(c.card, [...slots][0]) : null;
        };
        const r = c.plot.draw({
          t0, t1, series: g.series.map((x) => ({ id: x.sig, rs: feeder.ring(x.sig), slot: x.slot, dash: x.dash, axis: x.axis })),
          yRange: manualRange(c.ax.L.auto.checked, c.ax.L.min.value, c.ax.L.max.value),
          yRangeR: dual ? manualRange(c.ax.R.auto.checked, c.ax.R.min.value, c.ax.R.max.value) : null,
          axes: dual ? { L: { label: axisTitle(g, "L"), color: colorOf("L") }, R: { label: axisTitle(g, "R"), color: colorOf("R") } } : null,
          cursorT: cur, hoverT: hov, replay, xLabel, xTicks: ax.mode === "mission" ? ax.ticks : null, xTitle,
        });
        c.ax.L.lastY = c.lastY = r.yRange;
        c.ax.R.lastY = r.yRangeR;
        points += r.points;
        legendValues(c, cur, hov, nearMax);
      });
      frameMs = performance.now() - t0p;
      const span = t1 - t0;
      backBtn.disabled = zstack.length === 0;
      info.textContent = [status, paused ? `${replay ? "frozen" : "paused"} · ${fmtNum(span, 0)} s window` : replay ? `replay: whole recording, bar = replay position${Number.isFinite(windowS) ? ` · ${fmtNum(windowS, 0)} s window` : ""}` : "live", cursorT != null ? `${replay ? "hover" : "cursor"} ${xLabel(cursorT)}` : ""].filter(Boolean).join(" · ");
    }
    raf = requestAnimationFrame(frame);

    rebuild();
    if (ctx.api.state === "open") setTimeout(loadHistory, 300);
    const inst = {
      onPacket,
      onReset() {
        if (isReplay()) {
          // a seek: the whole recording stays loaded (once per recording), only a new log reloads it (frame loop)
          if (loadedKey === null) loadHistory();
          return;
        }
        if (paused) setPaused(false);
        loadHistory();
      },
      onReady: () => setTimeout(loadHistory, 300),
      onViewChange() {
        windowS = presetS = readWindow(ctx.view());
        syncWinBtns();
        loadedKey = null;
        setPaused(false);
        zstack.clear();
        feeder.clear();
        mclock.clear();
        loadHistory();
      },
      destroy() {
        cancelAnimationFrame(raf);
        for (const c of cards) c.plot.destroy();
      },
      freshness: () => ({ state: "fresh", age: null }),
      debug() {
        const rings = {};
        const ranges = {};
        for (const [k, r] of feeder.rings) {
          rings[k] = r.length;
          if (r.length) ranges[k] = [r.firstT, r.lastT];
        }
        return { ranges, graphs: graphs.map((g) => ({ title: g.title, series: g.series.map((x) => x.sig) })), windowS: Number.isFinite(windowS) ? windowS : "all", paused, viewT, axisMode, zoomDepth: zstack.length, ys: cards.map((c) => (c.yAuto.checked ? "auto" : [Number(c.yMin.value), Number(c.yMax.value)])), ysR: cards.map((c, i) => (hasRight(graphs[i].series) ? (c.ax.R.auto.checked ? "auto" : [Number(c.ax.R.min.value), Number(c.ax.R.max.value)]) : null)), axes: graphs.map((g) => g.series.map((x) => sideOf(x))), replayKey: loadedKey, replayT: ctx.replayPos() ?? ctx.status()?.replay?.t ?? null, replayStatusT: ctx.status()?.replay?.t ?? null, layerDraws: cards.map((c) => c.plot.layerDraws()), points, frameMs, rings, supplied: [...feeder.supplied], status };
      },
    };
    (window.exgs ??= {}).tuning = inst;
    return inst;

    function exportCsv(gi) {
      const now = ctx.now();
      if (now === null) return;
      const range = isReplay() ? replayRange() : null;
      const [t0, t1] = range ? replayWindow({ range, pos: now, windowS, frozen: paused && endT != null ? [endT - windowS, endT] : null }) : viewWindow({ now, windowS, follow: !paused, endT, firstT: Math.min(...[...feeder.rings.values()].filter((r) => r.length).map((r) => r.firstT), now) });
      const sel = gi === null ? graphs.flatMap((g) => g.series) : graphs[gi].series;
      const seen = new Set();
      const cols = [];
      for (const x of sel) {
        if (seen.has(x.sig)) continue;
        seen.add(x.sig);
        const s = SIGNAL_BY_ID.get(x.sig);
        cols.push({ name: `${s.label} [${s.unit}]`, rs: feeder.ring(x.sig) });
      }
      const blob = new Blob([toCsv(cols, t0, t1)], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      download(`exgs-${gi === null ? "graphs" : graphs[gi].preset || "graph"}-${new Date().toISOString().replace(/[:.]/g, "-")}.csv`, url);
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    }
    function exportPng(gi) {
      const c = cards[gi];
      const g = graphs[gi];
      const url = c.plot.toPng(`${g.title} [${hasRight(g.series) ? `${graphUnits({ series: seriesOn(g.series, "L") }).join(", ")} | ${graphUnits({ series: seriesOn(g.series, "R") }).join(", ")}` : graphUnits(g).join(", ")}]`, g.series.map((x) => ({ slot: x.slot, label: SIGNAL_BY_ID.get(x.sig).label })));
      download(`exgs-${g.preset || "graph"}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`, url);
    }
  },
});
