// GUI shell: websocket connections, store, layout, panel mounting (design section 5).
import { Api } from "./ws.js";
import { Store } from "./store.js";
import * as fmt from "./fmt.js";
import { h, Bindings } from "./ui.js";
import { allPanels } from "./panels/registry.js";
import "./panels/index.js";
import { normalizeLayout, pickPreset, stacks, subscriptionTypes } from "./layout.js";
import { createHeader, HEADER_CONSUMES } from "./header.js";

const TICK_MS = 100;
const store = new Store();
let view = "live";
let status = null;
let layout = null;
let presetName = null;
let mounted = []; // {def, entry, el, body, inst, titleEl, badgeEl}
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

// ---------------------------------------------------------------- connections
const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
let allApi = null;

const api = new Api({
  url: wsUrl,
  name: "gui",
  onState: (s) => {
    if (s !== "open" && rcState !== null) {
      rcState = null; // the core ended our lease with the connection; never show the old state as current
      for (const m of mounted) m.inst.onRc?.(null);
    }
    if (header) header.render();
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
  return [...mounted.map((m) => m.def), { consumes: HEADER_CONSUMES, mode: "latest" }];
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
      header.render();
      break;
    case "event":
      for (const m of mounted) m.inst.onEvent?.(msg);
      break;
    case "requests":
      for (const m of mounted) m.inst.onRequests?.(msg);
      break;
    case "rc":
      rcState = msg;
      for (const m of mounted) m.inst.onRc?.(msg);
      break;
    default:
  }
}

function startAllApi() {
  if (!mounted.some((m) => m.def.mode === "all")) return;
  allApi = new Api({
    url: wsUrl,
    name: "gui-console",
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

// ---------------------------------------------------------------- context
function makeCtx(entryEl) {
  return {
    store,
    schema: store.schema,
    api,
    fmt,
    h,
    view: () => view,
    status: () => status,
    rc: () => rcState,
    now: () => store.now(view),
    switchView,
    addViewGuard: (fn) => {
      viewGuards.add(fn);
      return () => viewGuards.delete(fn);
    },
    Bindings,
    setTitle: (t) => {
      const m = mounted.find((x) => x.el === entryEl);
      if (m) m.titleEl.textContent = t;
    },
  };
}
const ctxBase = () => makeCtx(null);

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
  store.clear(view);
  applyViewVisibility();
  for (const a of [api, allApi]) if (a) a.state === "open" && subscribe(a, a === api ? "latest" : "all");
  for (const m of mounted) m.inst.onViewChange?.(view);
  header.render();
}

// ---------------------------------------------------------------- layout and panels
let header = null;
const root = document.getElementById("panels");

function applyViewVisibility() {
  document.body.dataset.view = view;
  for (const m of mounted) m.el.hidden = !!(m.def.views && !m.def.views.includes(view));
}

function mountPreset(name) {
  for (const m of mounted) {
    try {
      m.inst.destroy?.();
    } catch (e) {
      console.error(e);
    }
  }
  mounted = [];
  presetName = name;
  lsSet("exgs.preset", name);
  const preset = layout.presets[name];
  const collapsedMap = JSON.parse(lsGet(`exgs.collapsed.${name}`) || "{}");
  root.replaceChildren();
  root.style.gridTemplateColumns = preset.columns;
  root.style.gridTemplateAreas = preset.areas.map((r) => `"${r}"`).join(" ");
  const defs = new Map(allPanels().map((d) => [d.id, d]));
  for (const [area, entries] of stacks(preset)) {
    const col = h("div", { class: "area", style: `grid-area: ${area}` });
    root.append(col);
    for (const entry of entries) {
      const def = defs.get(entry.panel);
      const titleEl = h("h2", { text: def.title });
      const badgeEl = h("span", { class: "badge-age" });
      const collapsed = entry.panel in collapsedMap ? collapsedMap[entry.panel] : entry.collapsed;
      const body = h("div", { class: "body" });
      const btn = h("button", { class: "fold", "aria-expanded": String(!collapsed), title: "collapse / expand" }, "▾");
      const head = h("header", {}, btn, titleEl, badgeEl);
      const el = h("section", { class: "panel", "data-panel": def.id, "data-collapsed": String(collapsed) }, head, body);
      const toggle = () => {
        const c = el.dataset.collapsed !== "true";
        el.dataset.collapsed = String(c);
        btn.setAttribute("aria-expanded", String(!c));
        collapsedMap[entry.panel] = c;
        lsSet(`exgs.collapsed.${name}`, JSON.stringify(collapsedMap));
      };
      btn.addEventListener("click", toggle);
      titleEl.addEventListener("click", toggle);
      col.append(el);
      const m = { def, entry, el, body, titleEl, badgeEl, inst: {} };
      mounted.push(m);
      try {
        m.inst = def.create(body, makeCtx(el)) || {};
      } catch (e) {
        console.error(`panel ${def.id} failed to start`, e);
        body.textContent = `Panel failed to start: ${e.message}`;
      }
    }
  }
  applyViewVisibility();
  // The subscription depends on the mounted panels
  if (api.state === "open") subscribe(api, "latest");
  if (allApi && allApi.state === "open") subscribe(allApi, "all");
  else if (!allApi) startAllApi();
  for (const m of mounted) m.inst.onViewChange?.(view);
}

// store -> panels
store.onChange((kind, v, item) => {
  if (v !== view) return;
  for (const m of mounted) {
    if (m.def.mode === "all") continue;
    if (kind === "snapshot") m.inst.onReset?.(v);
    else if (m.inst.onPacket && (m.def.consumes.includes(item.type) || m.def.consumes.includes("*"))) m.inst.onPacket(item);
  }
});

function tick() {
  const now = store.now(view);
  for (const m of mounted) {
    if (m.el.hidden) continue;
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
  header.render();
}

async function loadLayout() {
  let raw = null;
  try {
    const r = await fetch("/config/layout.json");
    if (r.ok) raw = await r.json();
  } catch (e) {
    /* fall back below */
  }
  const { layout: l, problems } = normalizeLayout(raw, allPanels().map((p) => p.id));
  for (const p of problems) console.warn("layout:", p);
  return l;
}

async function main() {
  layout = await loadLayout();
  header = createHeader(
    { ...ctxBase(), view: () => view, status: () => status, store, switchView },
    { onPreset: (n) => mountPreset(n), connState: () => api.state },
  );
  document.getElementById("header").replaceWith(header.el);
  header.setPresets(Object.entries(layout.presets).map(([id, p]) => ({ id, label: p.label })), "default");
  const first = pickPreset(layout, lsGet("exgs.preset"), window.innerWidth);
  header.setPresets(Object.entries(layout.presets).map(([id, p]) => ({ id, label: p.label })), first);
  mountPreset(first);
  api.start();
  setInterval(tick, TICK_MS);
}

main();
