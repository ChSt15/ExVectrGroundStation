// Mission status bar: always visible under the header, vehicle-reported values only (docs/gui.md).
// It shows what VEHICLE_STATUS says, never what the ground station sent: no optimistic state anywhere.
import { h } from "../ui.js";
import { compatibilityProblems, describeVehicleStatus, linkHealth } from "./status_logic.js";

export const STATUS_CONSUMES = ["VehicleStatus", "NetworkStatus", "LinkStats"];

export function createStatusBar(ctx) {
  const banner = h("div", { class: "sb-banner", role: "alert", hidden: true });
  const state = h("span", { class: "sb-state" });
  const cells = h("div", { class: "sb-cells" });
  const links = h("div", { class: "sb-links" });
  const el = h("div", { id: "statusbar", class: "statusbar", "data-testid": "status-bar" }, banner, h("div", { class: "sb-row" }, state, cells), links);
  const cellEls = new Map();

  function cell(it) {
    let c = cellEls.get(it.key);
    if (!c) {
      const v = h("span", { class: "sb-v" });
      c = { v, root: h("span", { class: "sb-cell", "data-key": it.key }, h("span", { class: "sb-l", text: it.label }), v) };
      cellEls.set(it.key, c);
      cells.append(c.root);
    }
    return c;
  }

  function render() {
    const view = ctx.view();
    const now = ctx.store.now(view) ?? Infinity;
    const schema = (t) => ctx.store.schema.get(t);
    const entry = ctx.store.get(view, "VehicleStatus");
    const d = describeVehicleStatus(entry, now, schema("VehicleStatus"));
    el.dataset.state = d.state;
    el.dataset.view = view;
    state.textContent =
      (view === "replay" ? "REPLAY  " : "") +
      (d.state === "missing" ? "no VehicleStatus received" : d.state === "stale" ? `STALE ${d.ageText}` : "vehicle status");
    state.title = d.src ? `from ${d.src}` : "";
    const seen = new Set();
    for (const it of d.items) {
      seen.add(it.key);
      const c = cell(it);
      c.v.textContent = it.text;
      c.root.dataset.tone = it.tone || "";
      c.root.dataset.loud = it.loud ? "true" : "false";
      c.root.title = it.title || "";
    }
    for (const [k, c] of cellEls) c.root.hidden = !seen.has(k);

    const status = ctx.status();
    const lookup = (id) => ctx.store.get(view, "LinkStats", id);
    const health = linkHealth(status ? status.sources : [], lookup, now, schema);
    links.replaceChildren(
      ...health.map((x) =>
        h("span", { class: "sb-link", "data-tone": x.tone, "data-stale": String(x.stale), title: `${x.id} (${x.kind}): ${x.state}` },
          h("span", { class: "dot" }), ` ${x.id} ${x.text}${x.stale ? ` (silent ${x.age.toFixed(1)} s)` : ""}`),
      ),
    );
    const problems = compatibilityProblems(ctx.store.all(view, "NetworkStatus"), now, schema("NetworkStatus"));
    banner.hidden = problems.length === 0;
    banner.textContent = problems.length ? `Version or schema mismatch: ${problems.join("; ")}` : "";
  }
  return { el, render };
}
