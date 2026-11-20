// Header bar: brand, layout views (Bench / Flight / Tuning), LIVE / REPLAY data view, RC state (as the core reports
// it), radio link quality, the recorder block (start / stop, name, duration, size, free space, errors), connection
// and theme. Recording is manual: Start begins a recording at once, Stop needs no confirmation.
import { h, makeCell, renderValue } from "./ui.js";
import { describe } from "./fmt.js";
import { describeRecorder } from "./status/source_logic.js";

export const HEADER_CONSUMES = ["RadioLinkStats"];

const RC_TEXT = { master: "MASTER", slave: "SLAVE", idle: "IDLE" };

export function createHeader(ctx, opts) {
  // layout views
  const viewBtns = new Map();
  const views = h("nav", { class: "seg views", role: "tablist", "aria-label": "layout view" });
  opts.views.forEach((v, i) => {
    const b = h("button", { role: "tab", text: v.label, title: `${v.hint || v.label} (Alt+${i + 1})`, "data-view": v.id, onclick: () => opts.onLayoutView(v.id) });
    viewBtns.set(v.id, b);
    views.append(b);
  });
  // data view
  const dataBtns = {};
  const toggle = h("div", { class: "toggle seg", role: "group", "aria-label": "data view" });
  for (const v of ["live", "replay"]) {
    dataBtns[v] = h("button", { text: v.toUpperCase(), "data-dv": v, onclick: () => ctx.switchView(v) });
    toggle.append(dataBtns[v]);
  }
  // RC and link chips
  const rc = h("span", { class: "chip rc-chip", "data-testid": "rc-chip", title: "RC state as the core reports it (docs/radio.md)" });
  rc.addEventListener("click", () => window.exgs?.dock?.open("radio"));
  const link = h("span", { class: "chip", title: "radio link quality (Router)" });
  const linkVal = makeCell();
  link.append(h("span", { class: "chip-l", text: "LQ" }), linkVal.el);
  // recorder block
  const recDot = h("span", { class: "rec-dot" });
  const recState = h("span", { class: "rec-state", "data-testid": "rec-state" }, recDot, h("span", { class: "rec-t" }));
  const recName = h("span", { class: "rec-name" });
  const recSize = h("span", { class: "rec-size" });
  const recFree = h("span", { class: "rec-free" });
  const recErr = h("span", { class: "rec-err", role: "alert" });
  const recMsg = h("span", { class: "rec-err" });
  const start = h("button", { class: "rec-start", "data-testid": "rec-start", title: "start recording now (manual only)" }, h("span", { class: "rec-dot" }), " Record");
  const stop = h("button", { class: "rec-stop", "data-testid": "rec-stop", title: "stop recording (no confirmation)" }, "■ Stop");
  const rec = h("div", { class: "rec", role: "group", "aria-label": "recording" }, recState, recName, recSize, recFree, start, stop, recErr, recMsg);
  async function recOp(op) {
    start.disabled = stop.disabled = true;
    try {
      await ctx.api.request(op, {});
      recMsg.textContent = "";
    } catch (e) {
      recMsg.textContent = `${op}: ${e.message}`;
    }
  }
  start.addEventListener("click", () => recOp("rec.start"));
  stop.addEventListener("click", () => recOp("rec.stop"));
  // connection, theme
  const conn = h("span", { class: "chip conn", "data-testid": "conn" }, h("span", { class: "dot" }), h("span", { class: "conn-t" }));
  const theme = h("button", { class: "theme", title: "switch dark / light (high contrast)", "aria-label": "switch dark or light theme", onclick: () => opts.onTheme() }, "◐");
  const el = h(
    "header",
    { class: "top" },
    h("strong", { class: "brand", text: "ExVectr GS" }),
    views,
    toggle,
    h("span", { class: "grow" }),
    rc,
    link,
    rec,
    conn,
    theme,
  );

  function setLayoutView(id) {
    for (const [v, b] of viewBtns) b.setAttribute("aria-selected", String(v === id));
  }

  function render() {
    const view = ctx.view();
    for (const v of Object.keys(dataBtns)) dataBtns[v].setAttribute("aria-pressed", String(v === view));
    const now = ctx.store.now(view);
    const dl = describe({ type: "RadioLinkStats", field: "lq_pct", digits: 0, unit: "%" }, (t, src) => ctx.store.get(view, t, src), now ?? Infinity, (t) => ctx.store.schema.get(t));
    renderValue(linkVal, dl, false);
    link.dataset.tone = dl.state === "fresh" ? (dl.value < 50 ? "bad" : dl.value < 80 ? "warn" : "ok") : "";
    // RC state: what the core reports it does (never inferred from clicks)
    const r = ctx.rc();
    const open = ctx.connState() === "open";
    rc.textContent = !open ? "RC —" : !r ? "RC n/a" : `RC ${RC_TEXT[r.mode] || r.mode}${r.mode === "master" && r.sending ? " · SENDING" : r.mode === "slave" ? " · Pocket" : ""}`;
    rc.dataset.mode = open && r ? r.mode : "";
    rc.dataset.sending = String(!!(open && r && r.sending));
    // recorder
    const st = ctx.status();
    const d = describeRecorder(st && st.recorder, st && st.clock);
    rec.dataset.active = String(d.active);
    rec.dataset.available = String(d.available);
    recState.dataset.active = String(d.active);
    recState.querySelector(".rec-t").textContent = !d.available ? "REC n/a" : d.active ? `REC ${d.duration}` : "not recording";
    recName.textContent = d.active ? d.name : "";
    recName.title = d.name || "";
    recSize.textContent = d.active ? d.size : "";
    recFree.textContent = d.free || "";
    recFree.dataset.tone = d.freeTone || "";
    recErr.textContent = d.error ? `recorder error: ${d.error}` : d.dropped ? `dropped ${d.dropped}` : "";
    start.hidden = !d.available || d.active;
    stop.hidden = !d.active;
    start.disabled = !open || !d.available;
    stop.disabled = !open;
    const cs = ctx.connState();
    conn.dataset.tone = cs === "open" ? "ok" : "bad";
    conn.querySelector(".conn-t").textContent = cs === "open" ? "core" : "core offline";
    conn.title = cs === "open" ? "connected to the core" : "disconnected, retrying";
  }
  return { el, render, setLayoutView };
}
