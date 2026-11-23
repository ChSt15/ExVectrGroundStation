// Mission status bar: always visible under the header, vehicle-reported values only (docs/gui.md).
// It shows what VEHICLE_STATUS says, never what the ground station sent: no optimistic state anywhere.
import { h } from "../ui.js";
import { compatibilityProblems, describeVehicleStatus, linkHealth } from "./status_logic.js";
import { powerReport } from "../link/link_logic.js";
import { lastSent } from "../telecommand/telecommand_logic.js";
import { isStale } from "../fmt.js";
import { describeAir } from "../rates/rates_logic.js";

export const SERIAL_HOP_HINT =
  "Frames per second received on the serial connection between the ground station and that device (the vehicle USB, or the Router/Pocket USB). It is NOT the radio rate: for the Router it includes the Pocket's RC_STATE copies, which stay on the USB (they never cross the air). See the Data rates tab.";

export const STATUS_CONSUMES = ["VehicleStatus", "NetworkStatus", "LinkStats", "LinkConfig", "TxModuleStats", "RxModuleStats"];
// The data-source tag at the start of the row shows VehicleStatus.simulation; the cells do not repeat it.
const IN_BANNER = new Set(["simulation"]);

export function createStatusBar(ctx) {
  const banner = h("div", { class: "sb-banner", role: "alert", hidden: true });
  const power = h("div", { class: "sb-banner", role: "alert", "data-testid": "power-alert", hidden: true });
  const state = h("span", { class: "sb-state" });
  const cells = h("div", { class: "sb-cells" });
  const links = h("div", { class: "sb-links" });
  const el = h("div", { id: "statusbar", class: "statusbar", "data-testid": "status-bar" }, banner, power, h("div", { class: "sb-row" }, state, cells), links);
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
    const sentRec = view === "live" ? lastSent(ctx.telecommands?.(), "ZeroEstimators") : null; // never in replay
    const zeroAge = sentRec && Number.isFinite(now) ? now - sentRec.t : null;
    const own16 = view === "live" ? ctx.rc?.()?.command?.session16 ?? null : null; // never compared in replay
    const d = describeVehicleStatus(entry, now, schema("VehicleStatus"), zeroAge, own16);
    el.dataset.state = d.state;
    el.dataset.view = view;
    state.textContent =
      (d.state === "missing" ? "no VehicleStatus received" : d.state === "stale" ? `STALE ${d.ageText}` : "vehicle status");
    state.title = d.src ? `from ${d.src}` : "";
    const seen = new Set();
    for (const it of d.items) {
      if (IN_BANNER.has(it.key)) continue;
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
    const air = describeAir(ctx.rates());
    const airChip = h("span", { class: "sb-link sb-air", "data-testid": "air-rate", "data-tone": air.tone, "data-known": String(air.known), title: air.known ? `radio path: ${air.long}` : "Payload bytes per second that crossed the air (frames from node 1 or 3 through the Router). Not measured in this view or without a radio source." }, ` ${air.text}`);
    links.replaceChildren(
      h("span", { class: "sb-link-label", "data-testid": "serial-hop-label", title: SERIAL_HOP_HINT, text: "frames/s on the serial hop:" }),
      ...health.map((x) =>
        h("span", { class: "sb-link", "data-tone": x.tone, "data-stale": String(x.stale), title: `${x.id} (${x.kind}): ${x.state}. ${SERIAL_HOP_HINT}` },
          h("span", { class: "dot" }), ` ${x.id} ${x.text}${x.stale ? ` (silent ${x.age.toFixed(1)} s)` : ""}`),
      ),
      airChip,
    );
    const problems = compatibilityProblems(ctx.store.all(view, "NetworkStatus"), now, schema("NetworkStatus"));
    banner.hidden = problems.length === 0;
    banner.textContent = problems.length ? `Version or schema mismatch: ${problems.join("; ")}` : "";
    // transmit power above the 10 dBm legal limit: always visible here, whichever dock tab is open
    // the latest values, stale ones too (a stale over-limit value stays visible, marked as stale)
    const fresh = (type) => ctx.store.get(view, type);
    const anyStale = ["LinkConfig", "TxModuleStats", "RxModuleStats"].some((t) => fresh(t) && isStale(fresh(t), now, schema(t)));
    const pr = powerReport({ cmd: fresh("LinkConfig")?.f.tx_power_dbm, router: fresh("TxModuleStats")?.f.tx_power, ship: fresh("RxModuleStats")?.f.tx_power });
    const bad = pr.warnings.filter((w) => w.tone === "bad");
    power.hidden = bad.length === 0;
    power.textContent = bad.length ? `TX POWER: ${bad.map((w) => w.text).join("; ")}${anyStale ? " (some values stale)" : ""}` : "";
  }
  return { el, render };
}
