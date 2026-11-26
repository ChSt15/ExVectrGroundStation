// Tuning view: graph workspace (docs/gui.md "Tuning view"). Ready-made graphs from the signal catalogue with a shared
// time axis (mission time T0 as the vehicle reports it, relative time when there is no mission clock) and cursor,
// window 10 s / 30 s / 2 min / all, pause with scroll-back, mouse zoom (box, wheel at the pointer, right-drag pan, double
// click reset, zoom stack, web/app/plot/zoom.js), signal picker per graph, auto or manual y range, legend with the value at the cursor, CSV and PNG export. History comes from the core's plot buffer
// (get_history buffer "plot": a page reload keeps the last 2 min; in replay it is read from the log), then every
// packet is appended (subscription mode "all"). Vehicle-reported values only; derived signals are display-only.
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { fmtClock, fmtNum } from "../fmt.js";
import { RingSeries, toCsv, viewWindow } from "../plot/series.js";
import {
  DEFAULT_GRAPHS, GRAPH_PRESETS, HISTORY_FIELDS, MAX_SERIES, SIGNALS, SIGNAL_BY_ID, SIGNAL_TYPES, SignalFeeder, graphUnits,
  historyToPackets, presetGraph, sanitizeGraphs, toggleSignal,
} from "../plot/catalogue.js";
import { createPlot } from "../plot/plot.js";
import { MissionClock, fmtT } from "../plot/missionclock.js";
import { missionAxis } from "../plot/missionclock.js";
import { ZoomStack, boxToRanges, MIN_SPAN_S, panWindow, panY, zoomAbout } from "../plot/zoom.js";

const WINDOWS = [["10 s", 10], ["30 s", 30], ["2 min", 120], ["all", Infinity]];
const LIVE_HISTORY_S = 130; // the core keeps about this much (PLOT_WINDOW_S)
const REPLAY_HISTORY_S = 600; // the core reads at most this much of a log per request
const RING = 16384; // > 130 s at 100 Hz
const FRAME_MS = 66; // about 15 redraws per second
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
    const feeder = new SignalFeeder(() => new RingSeries(RING));
    let graphs = sanitizeGraphs(JSON.parse(lsGet(STORE_KEY) || "null")) || DEFAULT_GRAPHS.map(presetGraph);
    let windowS = Number(lsGet(WIN_KEY)) || 30;
    if (!WINDOWS.some(([, s]) => s === windowS)) windowS = lsGet(WIN_KEY) === "all" ? Infinity : 30;
    let presetS = windowS; // the selected window preset, what a reset returns to
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
    const pauseBtn = h("button", { class: "pause", text: "❚❚ Pause", title: "freeze the graphs (data keeps arriving); right-drag or arrow keys to scroll back", onclick: () => setPaused(!paused) });
    const addSel = h("select", { "aria-label": "add a graph", onchange: () => { if (addSel.value) addGraph(addSel.value); addSel.value = ""; } },
      h("option", { value: "", text: "+ Add graph…" }), ...GRAPH_PRESETS.map((g) => h("option", { value: g.id, text: g.title })), h("option", { value: "__empty", text: "Empty graph" }));
    const resetBtn = h("button", { text: "Reset graphs", title: "back to the ready-made set", onclick: () => { graphs = DEFAULT_GRAPHS.map(presetGraph); save(); rebuild(); } });
    const backBtn = h("button", { text: "Back", title: "undo the last zoom step (Backspace or Escape on a graph)", disabled: true, onclick: () => zoomBack() });
    const zoomResetBtn = h("button", { text: "Reset zoom", title: "all graphs: y back to auto, time window back to the selected preset, live (double click on a graph does this for that graph's y)", onclick: () => resetZoom(null) });
    const csvAll = h("button", { text: "CSV (all)", title: "every signal of every graph in the visible window", onclick: () => exportCsv(null) });
    const info = h("span", { class: "hint tn-info", "data-testid": "tuning-info" });
    const mouseHint = h("div", { class: "hint tn-mouse", "data-testid": "tuning-mouse", text: "Mouse: left drag = zoom box (Shift time only, Ctrl y only) · right drag = pan · wheel = zoom at the pointer (Shift time only, Ctrl y only; y only on a manual-y graph) · double click = reset · Backspace / Esc = back" });
    el.append(h("div", { class: "toolbar tn-bar" }, h("span", { class: "seg", role: "group", "aria-label": "window" }, ...winBtns), pauseBtn, zoomResetBtn, backBtn, addSel, csvAll, resetBtn, info), mouseHint);
    const grid = h("div", { class: "tn-grid" });
    el.append(grid);

    const save = () => lsSet(STORE_KEY, JSON.stringify(graphs));
    function setWindow(s) {
      windowS = presetS = s;
      lsSet(WIN_KEY, Number.isFinite(s) ? String(s) : "all");
      syncWinBtns();
      if (!Number.isFinite(s) || s > 30) loadHistory();
    }
    const syncWinBtns = () => winBtns.forEach((b, i) => b.setAttribute("aria-pressed", String(WINDOWS[i][1] === windowS && windowS === presetS)));
    const dataFirst = () => {
      let first = Infinity;
      for (const r of feeder.rings.values()) if (r.length && r.firstT < first) first = r.firstT;
      return first;
    };
    const histSpan = () => (ctx.view() === "replay" ? REPLAY_HISTORY_S : LIVE_HISTORY_S);
    const snapshot = () => ({ windowS, paused, endT, y: cards.map((c) => ({ auto: c.yAuto.checked, lo: c.yMin.value, hi: c.yMax.value })) });
    function setManualY(c, lo, hi) {
      c.yMin.value = String(Number(lo.toPrecision(7)));
      c.yMax.value = String(Number(hi.toPrecision(7)));
      if (c.yAuto.checked) {
        c.yAuto.checked = false;
        c.yAuto.dispatchEvent(new Event("change"));
        c.yMin.value = String(Number(lo.toPrecision(7)));
        c.yMax.value = String(Number(hi.toPrecision(7)));
      }
    }
    function setAutoY(c) {
      if (c.yAuto.checked) return;
      c.yAuto.checked = true;
      c.yAuto.dispatchEvent(new Event("change"));
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
        });
      }
      syncWinBtns();
    }
    /** card = a card (its y only) or null (all graphs); the time window always goes back to the preset, live. */
    function resetZoom(card) {
      zstack.push(snapshot());
      for (const c of card ? [card] : cards) setAutoY(c);
      windowS = presetS;
      if (paused) setPaused(false);
      syncWinBtns();
    }
    function setPaused(p) {
      paused = p;
      endT = p ? ctx.now() : null;
      pauseBtn.textContent = p ? "▶ Live" : "❚❚ Pause";
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
      const yAuto = h("input", { type: "checkbox", checked: true, "aria-label": "automatic y range" });
      const yMin = h("input", { type: "number", step: "any", class: "tn-y", "aria-label": "y min", disabled: true, hidden: true });
      const yMax = h("input", { type: "number", step: "any", class: "tn-y", "aria-label": "y max", disabled: true, hidden: true });
      const pickBtn = h("button", { class: "small", text: "Signals ▾", "aria-expanded": "false" });
      const csvBtn = h("button", { class: "small", text: "CSV", onclick: () => exportCsv(gi) });
      const pngBtn = h("button", { class: "small", text: "PNG", onclick: () => exportPng(gi) });
      const delBtn = h("button", { class: "small", text: "✕", title: "remove graph", "aria-label": "remove graph", onclick: () => { graphs.splice(gi, 1); save(); rebuild(); } });
      const head = h("div", { class: "tn-head" }, title, unit, h("span", { class: "grow" }), h("label", { class: "tn-auto" }, yAuto, " auto y"), yMin, yMax, pickBtn, csvBtn, pngBtn, delBtn);
      const picker = h("div", { class: "tn-picker", hidden: true });
      const legend = h("div", { class: "tn-legend" });
      const plotBox = h("div", { class: "tn-plot" });
      const note = h("div", { class: "hint tn-note", text: g.note || "" });
      const card = h("section", { class: "tn-card", "data-graph": g.preset || `g${gi}` }, head, picker, plotBox, legend, note);
      grid.append(card);
      const plot = createPlot(plotBox, {
        onCursor: (t) => (cursorT = t),
        onPan: (dt, dv) => {
          const now = ctx.now();
          if (now === null) return;
          const [t0, t1] = panWindow(viewT[0], viewT[1], dt, dataFirst(), now);
          freezeWindow(t0, t1);
          if (dv && !c.yAuto.checked && c.lastY) setManualY(c, ...panY(c.lastY[0], c.lastY[1], dv));
        },
        onZoom: (f, o) => {
          const now = ctx.now();
          if (now === null) return;
          const mode = o?.mode || "both";
          if (mode !== "y") {
            const fx = o ? o.fx : 1; // keys: about the right edge, as before
            const [t0, t1] = zoomAbout(viewT[0], viewT[1], fx, f, { maxSpan: histSpan(), max: now });
            if (fx >= 0.98 && !paused) {
              windowS = t1 - t0; // anchored at the right edge: stay live
              syncWinBtns();
            } else freezeWindow(t0, t1);
          }
          if (o && mode !== "time" && !c.yAuto.checked && c.lastY) {
            const [y0, y1] = c.lastY;
            const mid = Math.max(Math.abs(y0), Math.abs(y1));
            setManualY(c, ...zoomAbout(y0, y1, o.fy, f, { minSpan: Math.max(1e-12, mid * 1e-9) }));
          }
        },
        onBox: (box, mode, geom) => {
          const now = ctx.now();
          if (now === null) return;
          const r = boxToRanges(box, geom.rect, geom.view, { mode, minT: dataFirst(), maxT: now, minSpan: MIN_SPAN_S });
          if (!r) return; // a click or a tiny drag
          zstack.push(snapshot());
          if (r.t) freezeWindow(r.t[0], r.t[1]);
          if (r.y) setManualY(c, r.y[0], r.y[1]);
        },
        onReset: () => resetZoom(c),
        onBack: () => zoomBack(),
      });
      yAuto.addEventListener("change", () => {
        yMin.disabled = yMax.disabled = yMin.hidden = yMax.hidden = yAuto.checked;
        if (!yAuto.checked && c.lastY) {
          yMin.value = c.lastY[0].toPrecision(4);
          yMax.value = c.lastY[1].toPrecision(4);
        }
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
          h("div", { class: "hint", text: `Up to ${MAX_SERIES} signals; grey = not received yet. One unit per graph reads best.` }),
          ...[...groups].map(([name, list]) =>
            h("fieldset", {}, h("legend", { text: name }), ...list.map((s) => {
              const cb = h("input", { type: "checkbox", checked: used.has(s.id) });
              cb.addEventListener("change", () => {
                graphs[gi] = toggleSignal(graphs[gi], s.id);
                save();
                c.refreshLegend();
                cb.checked = graphs[gi].series.some((x) => x.sig === s.id);
              });
              return h("label", { class: feeder.supplied.has(s.id) ? "" : "dim", title: s.desc || "" }, cb, ` ${s.label} [${s.unit}]`);
            })),
          ),
        );
      }
      const c = {
        card, plot, legend, unit, yAuto, yMin, yMax, lastY: null, items: [],
        refreshLegend() {
          const gr = graphs[gi];
          unit.textContent = graphUnits(gr).join(", ");
          c.items = gr.series.map((x) => {
            const s = SIGNAL_BY_ID.get(x.sig);
            const val = h("span", { class: "tn-val" });
            const sw = h("span", { class: `sw s${(x.slot % 8) + 1}${x.dash ? " dash" : ""}` });
            return { x, s, val, el: h("span", { class: "tn-item", title: s.desc || "" }, sw, h("span", { text: s.label }), val) };
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
      if (type === "MissionState") mclock.add(t, f.t_mission ?? null, f.mission_phase);
      feeder.push(type, t, f);
    }
    async function loadHistory() {
      const gen = ++histGen;
      const view = ctx.view();
      const now = ctx.now();
      if (now === null || ctx.api.state !== "open") return;
      loading = true;
      queue = [];
      const span = view === "replay" ? REPLAY_HISTORY_S : LIVE_HISTORY_S;
      const series = Object.entries(HISTORY_FIELDS).map(([type, fields]) => ({ type, fields }));
      status = "loading history…";
      try {
        const res = await ctx.api.request("get_history", { view, buffer: "plot", series, t0: now - span, t1: now + 1, max_points: 100000 }, 20000);
        if (gen !== histGen) return;
        feeder.clear();
        mclock.clear();
        const cal = ctx.store.get(view, "Calibration");
        if (cal) feed("Calibration", cal.t, cal.f);
        const home = ctx.store.get(view, "HomePosition"); // not in the plot history: the reference of the GNSS metres
        if (home) feed("HomePosition", home.t, home.f);
        for (const p of historyToPackets(res.series)) feed(p.type, p.t, p.f);
        for (const it of queue) feed(it.type, it.t, it.f);
        status = "";
      } catch (e) {
        if (gen === histGen) status = `history: ${e.message}`;
      } finally {
        if (gen === histGen) {
          loading = false;
          queue = [];
        }
      }
    }

    function onPacket(it) {
      if (loading) queue.push(it);
      else feed(it.type, it.t, it.f);
    }

    // ---------------------------------------------------------------- render loop
    let lastFrame = 0;
    let raf = 0;
    let points = 0;
    let frameMs = 0;
    function frame(ts) {
      raf = requestAnimationFrame(frame);
      if (ts - lastFrame < FRAME_MS || el.offsetParent === null || document.hidden) return;
      lastFrame = ts;
      const t0p = performance.now();
      const now = ctx.now();
      if (now === null) return;
      const view = ctx.view();
      const first = dataFirst();
      const [t0, t1] = viewWindow({ now, windowS, follow: !paused, endT, firstT: first });
      const rs = ctx.status()?.replay;
      viewT = [t0, t1];
      const ax = missionAxis(mclock, t0, t1, Math.max(3, Math.floor(el.clientWidth / 140)));
      axisMode = ax.mode;
      const relLabel = view === "replay" && rs && rs.t_start != null ? (t) => fmtClock(t - rs.t_start) : (t) => `${fmtNum(t - t1, 0)} s`;
      const xLabel = ax.mode === "mission" ? ax.label : relLabel;
      const xTitle = ax.mode === "mission" ? ax.title : view === "replay" ? "log time" : "t (s, relative to now)";
      info.title = ax.mode === "mission" ? ax.tip : "x axis: no mission clock (no MissionState time) in the loaded data, so time is relative: " + (view === "replay" ? "log time" : "seconds before the window end");
      const replayCursor = view === "replay" && rs && rs.t != null ? rs.t : null;
      const cur = cursorT ?? replayCursor;
      points = 0;
      cards.forEach((c, gi) => {
        const g = graphs[gi];
        let yr = null;
        if (!c.yAuto.checked) {
          const lo = Number(c.yMin.value);
          const hi = Number(c.yMax.value);
          if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo) yr = [lo, hi];
        }
        const r = c.plot.draw({ t0, t1, series: g.series.map((x) => ({ rs: feeder.ring(x.sig), slot: x.slot, dash: x.dash })), yRange: yr, cursorT: cur, xLabel, xTicks: ax.mode === "mission" ? ax.ticks : null, xTitle });
        c.lastY = r.yRange;
        points += r.points;
        for (const it of c.items) {
          const ring = feeder.ring(it.x.sig);
          const p = cur != null ? ring.nearest(cur, 0.5) : ring.length ? { t: ring.lastT, v: ring.vAt(ring.length - 1) } : null;
          it.val.textContent = p ? ` ${Number.isFinite(p.v) ? fmtNum(p.v, Math.abs(p.v) >= 100 ? 1 : Math.abs(p.v) >= 1 ? 2 : 3) : "NaN"} ${it.s.unit}` : feeder.supplied.has(it.x.sig) ? " —" : " not received";
          it.el.dataset.missing = String(!feeder.supplied.has(it.x.sig));
        }
      });
      frameMs = performance.now() - t0p;
      const span = t1 - t0;
      backBtn.disabled = zstack.length === 0;
      info.textContent = [status, paused ? `paused · ${fmtNum(span, 0)} s window` : view === "replay" ? "replay: cursor = replay position" : "live", cursorT != null ? `cursor ${xLabel(cursorT)}` : ""].filter(Boolean).join(" · ");
    }
    raf = requestAnimationFrame(frame);

    rebuild();
    if (ctx.api.state === "open") setTimeout(loadHistory, 300);
    const inst = {
      onPacket,
      onReset() {
        if (paused) setPaused(false);
        loadHistory();
      },
      onReady: () => setTimeout(loadHistory, 300),
      onViewChange() {
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
        return { ranges, graphs: graphs.map((g) => ({ title: g.title, series: g.series.map((x) => x.sig) })), windowS: Number.isFinite(windowS) ? windowS : "all", paused, viewT, axisMode, zoomDepth: zstack.length, ys: cards.map((c) => (c.yAuto.checked ? "auto" : [Number(c.yMin.value), Number(c.yMax.value)])), points, frameMs, rings, supplied: [...feeder.supplied], status };
      },
    };
    (window.exgs ??= {}).tuning = inst;
    return inst;

    function exportCsv(gi) {
      const now = ctx.now();
      if (now === null) return;
      const [t0, t1] = viewWindow({ now, windowS, follow: !paused, endT, firstT: Math.min(...[...feeder.rings.values()].filter((r) => r.length).map((r) => r.firstT), now) });
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
      const url = c.plot.toPng(`${g.title} [${graphUnits(g).join(", ")}]`, g.series.map((x) => ({ slot: x.slot, label: SIGNAL_BY_ID.get(x.sig).label })));
      download(`exgs-${g.preset || "graph"}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`, url);
    }
  },
});
