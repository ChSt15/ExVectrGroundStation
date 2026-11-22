// Vehicle group: all vehicle systems in one panel. Sensors, estimators, failure bits, failsafe and the calibration
// state as compact chips: green good, yellow warning, red failure, grey no data. The reason (state text, failure type,
// calibration reason) is the chip's tooltip. The mapping is the pure function in status/systems_logic.js. Display
// only. Failures keep "last set N s ago" in the tooltip so a short failure stays visible after it cleared.
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { describe, isStale } from "../fmt.js";
import { FAILURE_BITS } from "../status/status_logic.js";
import { CALIB_REFUSAL_NOTE, calibAnyBad, calibRows } from "../status/calib_rows.js";
import { systemsStatus, systemsSummary } from "../status/systems_logic.js";

registerPanel({
  id: "systems",
  title: "Systems",
  consumes: ["VehicleStatus", "Estimator", "Attitude", "Actuators", "Gps"],
  create(el, ctx) {
    const grid = h("div", { class: "sys-grid", "data-testid": "systems" });
    const calHint = h("div", { class: "note", "data-testid": "calib-hint", hidden: true, text: CALIB_REFUSAL_NOTE });
    el.append(grid, calHint);
    const lastSet = {};
    const chips = new Map();
    let group = null;
    const chip = (x) => {
      let c = chips.get(x.key);
      if (!c) {
        if (x.group !== group) {
          if (group !== null) grid.append(h("span", { class: "sys-sep" }));
          group = x.group;
        }
        c = h("span", { class: "sys-chip", "data-key": x.key, "data-group": x.group }, h("span", { class: "dot" }), h("span", { class: "sys-l", text: x.label }));
        chips.set(x.key, c);
        grid.append(c);
      }
      return c;
    };
    const reset = () => {
      for (const k of Object.keys(lastSet)) delete lastSet[k];
    };
    const render = () => {
      const v = ctx.view();
      const now = ctx.store.now(v);
      const get = (t) => ctx.store.get(v, t);
      const sch = (t) => ctx.store.schema.get(t);
      const e = get("VehicleStatus");
      const d = describe({ type: "VehicleStatus", field: "failure_bits" }, (t, src) => ctx.store.get(v, t, src), now ?? Infinity, sch);
      const statusState = !e ? "missing" : d.state === "stale" ? "stale" : "fresh";
      const g = get("Gps");
      const gpsFresh = g && now !== null && !isStale(g, now, sch("Gps"));
      const ev = get("Estimator");
      const at = get("Attitude");
      const ac = get("Actuators");
      const co = ctx.callouts && ctx.callouts();
      const items = systemsStatus({
        status: e ? e.f : null,
        statusState,
        gps: gpsFresh ? g.f : null,
        estimator: ev ? ev.f : null,
        attitude: at ? at.f : null,
        groundDistance: ac ? ac.f.ground_distance : null,
        calib: co ? co.log.calibState() : null,
        lastSet,
        now,
      });
      for (const x of items) {
        const c = chip(x);
        c.dataset.tone = x.tone;
        c.title = x.reason;
      }
      const s = systemsSummary(items);
      ctx.setTitle(!e && !g ? "Systems" : s.bad ? `Systems (${s.bad} FAILURE)` : s.warn ? `Systems (${s.warn} warning)` : "Systems (all good)");
      const cr = calibRows(co ? co.log.calibState() : null, statusState === "fresh" && e ? e.f : null);
      calHint.hidden = !calibAnyBad(cr);
    };
    return {
      onPacket(pkt) {
        if (pkt.type !== "VehicleStatus") return;
        const f = pkt.f;
        FAILURE_BITS.forEach((_, i) => {
          if (typeof f.failure_bits === "number" && (f.failure_bits & (1 << i)) !== 0) lastSet[i] = pkt.t;
        });
        if (f.failsafe === true) lastSet.failsafe = pkt.t;
      },
      onReset: reset,
      onViewChange() {
        reset();
        render();
      },
      onTick: render,
      freshness: () => {
        const v = ctx.view();
        const d = describe({ type: "VehicleStatus", field: "failure_bits" }, (t, src) => ctx.store.get(v, t, src), ctx.store.now(v) ?? Infinity, (t) => ctx.store.schema.get(t));
        return { state: d.state === "not supplied" ? "fresh" : d.state, age: d.state === "stale" ? d.age : null };
      },
    };
  },
});
