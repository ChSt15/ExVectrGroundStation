// GUI shell (design section 5, redesign 2026-11-20): connections, store, top area (data-source banner, header,
// status bar), the view's groups and the Operations dock. docs/gui.md describes the structure.
import { Api } from "./ws.js";
import { Store } from "./store.js";
import * as fmt from "./fmt.js";
import { h, Bindings } from "./ui.js";
import { allPanels, getPanel } from "./panels/registry.js";
import "./panels/index.js";
import { dockState, normalizeLayout, pickTab, pickView, subscriptionTypes, viewPanels } from "./layout.js";
import { createHeader, HEADER_CONSUMES } from "./header.js";
import { createStatusBar, STATUS_CONSUMES } from "./status/status_bar.js";
import { createSourceBanner, BANNER_CONSUMES } from "./status/source_banner.js";
import { createCallouts, CALLOUT_CONSUMES } from "./callouts/controller.js";
import { Notifier } from "./callouts/notify.js";
import { Speaker } from "./callouts/speech.js";

// Hold-to-confirm buttons (telecommands, requests, unlock) need an uninterrupted press of 1 s. Touch screens and the
// Steam Frame's controller pointer turn a press held for about 500 ms into a context-menu (right click), which cancels
// the pointer and the hold. The browser menu is never wanted in this app (text fields keep it for copy and paste).
document.addEventListener("contextmenu", (e) => {
  const t = e.target;
  if (t instanceof Element && t.closest("input, textarea, [contenteditable]")) return;
  e.preventDefault();
});

const TICK_MS = 100;
const store = new Store();
let view = "live"; // data view: live or replay (the core's views)
let status = null;
let layout = null;
let viewId = null; // layout view: bench, flight, tuning
let dockMode = "open"; // open | rail | overlay
let dockTab = null;
let mounted = []; // {def, entry, el, body, inst, titleEl, badgeEl, area: "main"|"dock"|"fixed"}
let ratesData = null; // newest `get_rates` reply for the current data view (polled once per second)
let rcState = null; // newest `rc` push from the core (what it actually sends / mirrors); null while disconnected
const viewGuards = new Set(); // async (nextView) => boolean; a panel can veto or prepare a view switch (radio: release prompt)

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
    /* storage unavailable: the GUI still works */
  }
};

// ---------------------------------------------------------------- theme (dark control-room default, light high contrast)
function applyTheme(t) {
  document.documentElement.dataset.theme = t === "light" ? "light" : "dark";
  lsSet("exgs.theme", document.documentElement.dataset.theme);
  window.dispatchEvent(new Event("exgs-theme"));
}
applyTheme(lsGet("exgs.theme") || "dark");

// ---------------------------------------------------------------- connections
const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
let allApi = null;
let callouts = null; // callout controller (docs/callouts.md), display only
let notifier = null;
let speaker = null;
let updatePush = null; // last `update` push (self-update summary: behind count, restart pending; docs/updating.md)
let tcPush = null; // last `telecommands` push (TEMPORARY, docs/telecommands.md)

const api = new Api({
  url: wsUrl,
  name: "gui",
  onState: (s) => {
    if (s !== "open" && rcState !== null) {
      rcState = null; // the core ended our lease with the connection; never show the old state as current
      for (const m of mounted) m.inst.onRc?.(null);
    }
    renderTop();
  },
  onReady: () => {
    subscribe(api, "latest");
    for (const m of mounted) m.inst.onReady?.();
  },
  onMessage: onMainMessage,
});

function subscribe(a, mode) {
  const types = subscriptionTypes(neededPanels(), mode);
  if (Array.isArray(types) && types.length === 0) return;
  a.request("subscribe", { view, types, mode, max_rate_hz: mode === "all" ? 30 : 20 }).catch(() => {});
}

function neededPanels() {
  return [...mounted.map((m) => m.def), { consumes: [...HEADER_CONSUMES, ...STATUS_CONSUMES, ...BANNER_CONSUMES, ...CALLOUT_CONSUMES], mode: "latest" }];
}

function onMainMessage(msg) {
  switch (msg.op) {
    case "welcome":
      store.setClock("live", msg.clock);
      break;
    case "schema":
      store.setSchema(msg.types || []);
      break;
    case "snapshot":
      store.applySnapshot(msg.view, msg);
      break;
    case "packets":
      store.applyPackets(msg.view, msg);
      break;
    case "status":
      status = msg;
      store.setClock("live", msg.clock);
      if (msg.replay && typeof msg.replay.t === "number") {
        store.setClock("replay", msg.replay.t, msg.replay.state === "playing" ? msg.replay.speed : 0);
      }
      for (const m of mounted) m.inst.onStatus?.(status);
      renderTop();
      break;
    case "event":
      for (const m of mounted) m.inst.onEvent?.(msg);
      break;
    case "requests":
      for (const m of mounted) m.inst.onRequests?.(msg);
      break;
    case "telecommands":
      tcPush = msg;
      statusBar?.render(); // the status bar shows "ZeroEstimators sent N s ago" from this record
      for (const m of mounted) m.inst.onTelecommands?.(msg);
      break;
    case "update":
      updatePush = msg;
      for (const m of mounted) m.inst.onUpdate?.(msg);
      header?.render();
      break;
    case "update_progress":
      for (const m of mounted) m.inst.onUpdateProgress?.(msg);
      break;
    case "rc":
      rcState = msg;
      for (const m of mounted) m.inst.onRc?.(msg);
      header?.render();
      break;
    default:
  }
}

function ensureAllApi() {
  if (allApi || !mounted.some((m) => m.def.mode === "all")) return;
  allApi = new Api({
    url: wsUrl,
    name: "gui-all",
    onReady: () => subscribe(allApi, "all"),
    onMessage(msg) {
      if (msg.op === "snapshot" && msg.view === view) {
        for (const m of mounted) if (m.def.mode === "all") m.inst.onReset?.(view);
      }
      if (msg.op !== "packets" || msg.view !== view) return;
      for (const it of msg.items || []) {
        for (const m of mounted) {
          if (m.def.mode === "all" && m.inst.onPacket && (m.def.consumes.includes(it.type) || m.def.consumes.includes("*"))) m.inst.onPacket(it);
        }
      }
    },
  });
  allApi.start();
}

function resubscribe() {
  if (api.state === "open") subscribe(api, "latest");
  if (allApi && allApi.state === "open") subscribe(allApi, "all");
  else ensureAllApi();
}

// ---------------------------------------------------------------- context
function makeCtx(entry, getM) {
  return {
    store,
    schema: store.schema,
    api,
    fmt,
    h,
    options: (entry && entry.options) || {},
    view: () => view,
    status: () => status,
    rc: () => rcState,
    rates: () => ratesData,
    telecommands: () => tcPush,
    update: () => updatePush,
    callouts: () => callouts,
    notifier: () => notifier,
    speaker: () => speaker,
    now: () => store.now(view),
    switchView,
    addViewGuard: (fn) => {
      viewGuards.add(fn);
      return () => viewGuards.delete(fn);
    },
    Bindings,
    setTitle: (t) => {
      const m = getM && getM();
      if (m) m.titleEl.textContent = t;
    },
  };
}

async function pollRates() {
  if (api.state !== "open") return;
  const v = view;
  try {
    const d = await api.request("get_rates", { view: v }, 5000);
    if (v !== view) return; // the view changed meanwhile
    ratesData = d;
    for (const m of mounted) m.inst.onRates?.(d);
  } catch (e) {
    /* older core or busy: keep the last values */
  }
}

async function switchView(next) {
  if (next === view || !["live", "replay"].includes(next)) return;
  for (const guard of [...viewGuards]) {
    try {
      if (!(await guard(next))) return;
    } catch (e) {
      console.error("view guard", e);
      return;
    }
  }
  if (next === view) return;
  for (const a of [api, allApi]) a?.request("unsubscribe", { view }).catch(() => {});
  view = next;
  ratesData = null;
  store.clear(view);
  applyViewVisibility();
  for (const a of [api, allApi]) if (a && a.state === "open") subscribe(a, a === api ? "latest" : "all");
  for (const m of mounted) m.inst.onViewChange?.(view);
  renderTop();
  pollRates();
}

// ---------------------------------------------------------------- panels
let header = null;
let statusBar = null;
let banner = null;
const root = document.getElementById("panels");
const dockEl = document.getElementById("dock");

function applyViewVisibility() {
  document.body.dataset.view = view;
  for (const m of mounted) m.el.hidden = (m.def.views && !m.def.views.includes(view)) || (m.area === "dock" && m.def.id !== dockTab);
}

/** Create a panel element and its instance. */
function mountPanel(def, entry, parent, area, collapsedMap, collapsedKey) {
  const titleEl = h("h2", { text: def.title, id: `pt-${def.id}` });
  const badgeEl = h("span", { class: "badge-age" });
  const collapsed = area === "main" && (def.id in collapsedMap ? collapsedMap[def.id] : entry.collapsed);
  const body = h("div", { class: "body" });
  const btn = h("button", { class: "fold", "aria-expanded": String(!collapsed), "aria-label": `collapse or expand ${def.title}`, title: "collapse / expand" }, "▾");
  const head = h("header", {}, area === "main" ? btn : null, titleEl, badgeEl);
  const el = h("section", { class: "panel", "data-panel": def.id, "data-area": area, "data-collapsed": String(!!collapsed), "aria-labelledby": `pt-${def.id}` }, head, body);
  if (entry.options && entry.options.height === "fill") el.classList.add("fill");
  if (area === "main") {
    const toggle = () => {
      const c = el.dataset.collapsed !== "true";
      el.dataset.collapsed = String(c);
      btn.setAttribute("aria-expanded", String(!c));
      collapsedMap[def.id] = c;
      lsSet(collapsedKey, JSON.stringify(collapsedMap));
    };
    btn.addEventListener("click", toggle);
    titleEl.addEventListener("click", toggle);
  }
  parent.append(el);
  const m = { def, entry, el, body, titleEl, badgeEl, area, inst: {} };
  mounted.push(m);
  try {
    m.inst = def.create(body, makeCtx(entry, () => m)) || {};
  } catch (e) {
    console.error(`panel ${def.id} failed to start`, e);
    body.textContent = `Panel failed to start: ${e.message}`;
  }
  return m;
}

function destroy(list) {
  for (const m of list) {
    try {
      m.inst.destroy?.();
    } catch (e) {
      console.error(e);
    }
  }
}

/** Mount the groups of a layout view. Dock and fixed panels stay mounted (a held RC lease is not touched). */
function mountView(id) {
  destroy(mounted.filter((m) => m.area === "main"));
  mounted = mounted.filter((m) => m.area !== "main");
  viewId = id;
  lsSet("exgs.view", id);
  const v = layout.views[id];
  document.body.dataset.layout = id;
  const collapsedKey = `exgs.collapsed.${id}`;
  let collapsedMap = {};
  try {
    collapsedMap = JSON.parse(lsGet(collapsedKey) || "{}") || {};
  } catch (e) {
    collapsedMap = {};
  }
  root.replaceChildren();
  root.style.gridTemplateColumns = v.columns;
  root.dataset.groups = String(v.groups.length);
  for (const g of v.groups) {
    const col = h("div", { class: "group", "data-group": g.id, role: "region", "aria-label": g.title || g.id });
    if (g.title) col.append(h("div", { class: "group-title" }, h("span", { text: g.title })));
    root.append(col);
    for (const entry of g.panels) {
      const def = getPanel(entry.panel);
      if (def) mountPanel(def, entry, col, "main", collapsedMap, collapsedKey);
    }
  }
  setDockMode(dockState(v, lsGet(`exgs.dock.${id}`)), false);
  applyViewVisibility();
  resubscribe();
  for (const m of mounted) if (m.area === "main") m.inst.onViewChange?.(view);
  header?.setLayoutView(id);
  for (const m of mounted) if (m.area === "main") m.inst.onStatus?.(status);
}

// ---------------------------------------------------------------- Operations dock
const dockTabs = new Map(); // panel id -> tab button
let dockBody = null;
let dockLock = null;

function buildDock() {
  const tabs = layout.dock.tabs;
  if (!tabs.length) {
    dockEl.hidden = true;
    return;
  }
  const tablist = h("div", { class: "dock-tabs", role: "tablist", "aria-label": layout.dock.title, "aria-orientation": "vertical" });
  const collapse = h("button", { class: "dock-collapse", title: "collapse the dock to the side rail", "aria-label": "collapse the Operations dock", onclick: () => setDockMode(dockMode === "open" ? "rail" : "open", true) }, "⟩");
  const close = h("button", { class: "dock-close", title: "close (Escape)", "aria-label": "close the Operations dock", onclick: () => setDockMode("rail", false) }, "✕");
  const head = h("div", { class: "dock-head" }, h("span", { class: "dock-title", text: layout.dock.title }), close, collapse);
  dockLock = h("div", { class: "dock-lock", "data-testid": "dock-lock", hidden: true });
  dockBody = h("div", { class: "dock-body" });
  dockEl.replaceChildren(head, tablist, dockLock, dockBody);
  for (const t of tabs) {
    const def = getPanel(t.panel);
    if (!def) continue;
    const dot = h("span", { class: "tab-dot", hidden: true });
    const b = h("button", { class: "dock-tab", role: "tab", "aria-selected": "false", "data-tab": t.panel, title: t.label }, h("span", { class: "tab-l", text: t.label }), dot);
    b.addEventListener("click", () => openTab(t.panel));
    tablist.append(b);
    dockTabs.set(t.panel, { b, dot });
    mountPanel(def, t, dockBody, "dock", {}, "");
  }
  tablist.addEventListener("keydown", (e) => {
    const ids = [...dockTabs.keys()];
    const i = ids.indexOf(dockTab);
    if (e.key === "ArrowDown" || e.key === "ArrowRight") openTab(ids[(i + 1) % ids.length]);
    else if (e.key === "ArrowUp" || e.key === "ArrowLeft") openTab(ids[(i - 1 + ids.length) % ids.length]);
    else return;
    e.preventDefault();
    dockTabs.get(dockTab)?.b.focus();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && dockMode === "overlay" && !document.querySelector(".rc-modal")) setDockMode("rail", false);
  });
  selectTab(pickTab(layout, lsGet("exgs.dock.tab")));
}

function selectTab(id) {
  dockTab = id;
  lsSet("exgs.dock.tab", id);
  for (const [pid, t] of dockTabs) {
    t.b.setAttribute("aria-selected", String(pid === id));
    t.b.tabIndex = pid === id ? 0 : -1;
  }
  applyViewVisibility();
  renderDockLock();
}

/** Show a dock tab: in rail mode the dock opens as an overlay. */
function openTab(id) {
  if (!dockTabs.has(id)) return;
  selectTab(id);
  if (dockMode === "rail") setDockMode("overlay", false);
}

function setDockMode(mode, remember) {
  dockMode = mode;
  dockEl.dataset.mode = mode;
  document.body.dataset.dock = mode;
  if (remember && viewId) lsSet(`exgs.dock.${viewId}`, mode === "overlay" ? "rail" : mode);
}

function renderDockLock() {
  if (!dockLock) return;
  const m = mounted.find((x) => x.area === "dock" && x.def.id === dockTab);
  const locked = banner ? banner.state() : null;
  const show = !!(m && m.def.inputs && locked && locked.inputsLocked);
  dockLock.hidden = !show;
  dockLock.textContent = show ? `${locked.lockReason}: nothing can be sent from here` : "";
}

function renderDockBadges() {
  for (const m of mounted) {
    if (m.area !== "dock") continue;
    const t = dockTabs.get(m.def.id);
    const a = m.inst.alert?.();
    t.dot.hidden = !a;
    t.dot.dataset.tone = a || "";
    t.b.dataset.alert = a || "";
  }
}

// ---------------------------------------------------------------- callouts: "Enable audio" in the notification area
function renderAudioBar() {
  const bar = notifier.bar;
  const btn = h("button", { class: "toast-audio", "data-testid": "toast-audio-enable", onclick: () => callouts.enableAudio() }, "🔇 Enable audio");
  bar.append(btn);
  const sync = () => {
    const s = callouts.audioState();
    bar.hidden = s === "on" || s === "unsupported" || !callouts.settings().master;
    btn.textContent = s === "blocked" ? "🔇 Audio blocked: click to enable" : "🔇 Enable audio";
    bar.dataset.state = s;
    header?.render();
  };
  callouts.onChange(sync);
  sync();
}

// ---------------------------------------------------------------- tick
store.onChange((kind, v, item) => {
  if (v !== view) return;
  if (kind === "snapshot" && v === "replay") callouts?.onSeek(); // a seek: re-baseline silently
  for (const m of mounted) {
    if (m.def.mode === "all") continue;
    if (kind === "snapshot") m.inst.onReset?.(v);
    else if (m.inst.onPacket && (m.def.consumes.includes(item.type) || m.def.consumes.includes("*"))) m.inst.onPacket(item);
  }
});

const visible = (m) => !m.el.hidden && (m.area !== "dock" || dockMode !== "rail") && m.el.dataset.collapsed !== "true";

function renderTop() {
  banner?.render();
  header?.render();
  statusBar?.render();
  renderDockLock();
}

function tick() {
  const now = store.now(view);
  for (const m of mounted) {
    if (!visible(m) && m.def.id !== "radio") continue; // the radio keeps its own state rendering
    try {
      m.inst.onTick?.(now);
      const f = m.inst.freshness?.();
      if (f) {
        m.badgeEl.textContent = f.state === "stale" ? `stale ${fmt.fmtAge(f.age)}` : f.state === "missing" ? "no data" : "";
        m.badgeEl.dataset.state = f.state;
      }
    } catch (e) {
      console.error(`panel ${m.def.id} tick`, e);
    }
  }
  renderTop();
  renderDockBadges();
  try {
    callouts?.tick();
  } catch (e) {
    console.error("callouts", e);
  }
}

async function loadLayout() {
  let raw = null;
  try {
    const r = await fetch("/config/layout.json");
    if (r.ok) raw = await r.json();
  } catch (e) {
    /* fall back below */
  }
  const ids = allPanels().map((p) => p.id).filter((id) => id !== "transport");
  const { layout: l, problems } = normalizeLayout(raw, ids);
  for (const p of problems) console.warn("layout:", p);
  return l;
}

async function loadCalloutDefaults() {
  try {
    const r = await fetch("/config/callouts.json");
    if (r.ok) return await r.json();
  } catch (e) {
    /* built-in defaults */
  }
  return null;
}

async function main() {
  layout = await loadLayout();
  notifier = new Notifier();
  speaker = new Speaker({ getSettings: () => callouts.settings() });
  callouts = createCallouts({ store, getView: () => view, getCoreStatus: () => status, ls: { get: lsGet, set: lsSet }, speaker, notifier, defaults: await loadCalloutDefaults() });
  renderAudioBar();
  const base = makeCtx(null, null);
  banner = createSourceBanner({ ...base, connState: () => api.state });
  header = createHeader(
    { ...base, connState: () => api.state, theme: () => document.documentElement.dataset.theme },
    {
      views: Object.entries(layout.views).map(([id, v]) => ({ id, label: v.label, hint: v.hint })),
      onLayoutView: (id) => mountView(id),
      callouts: () => callouts,
      onTheme: () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"),
    },
  );
  document.getElementById("header").replaceWith(header.el);
  statusBar = createStatusBar({ ...base });
  document.getElementById("statusbar").replaceWith(statusBar.el);
  statusBar.el.querySelector(".sb-row").prepend(banner.el); // the data-source tag leads the vehicle status row
  statusBar.el.after(banner.strip); // replay: compact strip with log, position and transport
  // replay transport: a fixed panel inside the banner (visible in the replay view only)
  const transport = getPanel("transport");
  if (transport) mountPanel(transport, { panel: "transport", options: {} }, banner.transportSlot, "fixed", {}, "");
  buildDock();
  mountView(pickView(layout, lsGet("exgs.view")));
  window.exgs = Object.assign(window.exgs || {}, {
    dock: { open: openTab, mode: () => dockMode, tab: () => dockTab, set: (m) => setDockMode(m, false) },
    layout: { view: () => viewId, set: (id) => layout.views[id] && mountView(id), views: () => Object.keys(layout.views) },
    source: () => banner.state(),
    callouts: () => callouts,
  });
  document.addEventListener("keydown", (e) => {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const ids = Object.keys(layout.views);
    const i = Number(e.key) - 1;
    if (i >= 0 && i < ids.length) {
      e.preventDefault();
      mountView(ids[i]);
    }
  });
  api.start();
  setInterval(tick, TICK_MS);
  setInterval(pollRates, 1000);
  pollRates();
}

main();
