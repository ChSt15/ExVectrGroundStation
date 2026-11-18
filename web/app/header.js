// Header bar: view switch, per-source rate, radio link, recording state, connection, layout preset.
import { h, makeCell, renderValue } from "./ui.js";
import { describe, fmtClock } from "./fmt.js";

export const HEADER_CONSUMES = ["LinkStats", "RadioLinkStats"];

export function createHeader(ctx, opts) {
  const viewBtns = {};
  const toggle = h("div", { class: "toggle", role: "group", "aria-label": "view" });
  for (const v of ["live", "replay"]) {
    viewBtns[v] = h("button", { text: v.toUpperCase(), onclick: () => ctx.switchView(v) });
    toggle.append(viewBtns[v]);
  }
  const srcBox = h("span", { class: "chips" });
  const link = h("span", { class: "chip", "data-tone": "" });
  const linkVal = makeCell();
  link.append("link ", linkVal.el);
  const rec = h("span", { class: "chip rec", text: "" });
  const conn = h("span", { class: "chip conn", text: "connecting" });
  const preset = h("select", { title: "layout preset", "aria-label": "layout preset", onchange: () => opts.onPreset(preset.value) });
  const el = h("header", { class: "top" }, h("strong", { class: "brand", text: "ExVectr GS" }), toggle, srcBox, link, rec, conn, preset);

  let srcKey = "";
  const srcCells = new Map();

  function setPresets(names, current) {
    preset.replaceChildren(...names.map((n) => h("option", { value: n.id, text: n.label })));
    preset.value = current;
  }

  function render() {
    const view = ctx.view();
    for (const v of Object.keys(viewBtns)) viewBtns[v].setAttribute("aria-pressed", String(v === view));
    const status = ctx.status();
    const sources = status ? status.sources : [];
    const key = sources.map((s) => s.id).join(",");
    if (key !== srcKey) {
      srcKey = key;
      srcCells.clear();
      srcBox.replaceChildren();
      for (const s of sources) {
        const dot = h("span", { class: "dot" });
        const cell = makeCell();
        const chip = h("span", { class: "chip" }, s.id, " ", dot, " ", cell.el);
        srcBox.append(chip);
        srcCells.set(s.id, { dot, cell, chip });
      }
    }
    const now = ctx.store.now(view);
    for (const s of sources) {
      const c = srcCells.get(s.id);
      c.dot.dataset.tone = { connected: "ok", searching: "warn", error: "bad" }[s.state] || "";
      c.chip.title = `${s.id}: ${s.state}${s.device ? ` ${s.device}` : ""}`;
      const d = describe({ type: "LinkStats", src: s.id, field: "rx_frames_s", digits: 0, unit: "pkt/s" }, (t, src) => ctx.store.get(view, t, src), now ?? Infinity, (t) => ctx.store.schema.get(t));
      renderValue(c.cell, d, false);
    }
    const dl = describe({ type: "RadioLinkStats", field: "lq_pct", digits: 0, unit: "%" }, (t, src) => ctx.store.get(view, t, src), now ?? Infinity, (t) => ctx.store.schema.get(t));
    renderValue(linkVal, dl, false);
    link.dataset.tone = dl.state === "fresh" ? "ok" : "";
    const r = status && status.recorder;
    if (r && r.active) {
      rec.textContent = `REC ● ${r.started && status.clock ? fmtClock(status.clock - r.started) : ""} ${r.name || ""}`;
      rec.dataset.tone = "bad";
    } else {
      rec.textContent = r && r.available !== false ? "REC ○" : "";
      rec.dataset.tone = "";
    }
    const cs = opts.connState();
    conn.textContent = cs === "open" ? "connected" : "disconnected, retrying";
    conn.dataset.tone = cs === "open" ? "ok" : "bad";
  }
  return { el, render, setPresets };
}
