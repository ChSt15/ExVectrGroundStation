// Vehicle group: Health panel. Issues (one row per system that is not good, with the issue text, yellow warning / red
// failure, most severe first), Good (green chips), No data (grey chips). The reason is the item tooltip. The mapping is
// the pure function in status/systems_logic.js. Display only. Failures keep "last set N s ago" in the tooltip so a
// short failure stays visible after it cleared.
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { describe, isStale } from "../fmt.js";
import { FAILURE_BITS } from "../status/status_logic.js";
import { CALIB_REFUSAL_NOTE, calibAnyBad, calibRows } from "../status/calib_rows.js";
import { healthSections, systemsStatus, systemsSummary } from "../status/systems_logic.js";

registerPanel({
  id: "health",
  title: "Health",
  consumes: ["VehicleStatus", "Estimator", "Attitude", "Position", "Gps"],
  create(el, ctx) {
    const issuesEl = h("div", { class: "hl-issues", "data-testid": "health-issues" });
    const goodEl = h("div", { class: "hl-chips hl-good", "data-testid": "health-good" });
    const noDataEl = h("div", { class: "hl-chips hl-nodata", "data-testid": "health-nodata" });
    const calHint = h("div", { class: "note", "data-testid": "calib-hint", hidden: true, text: CALIB_REFUSAL_NOTE });
    el.append(
      h("div", { class: "hl-h" }, "Issues"),
      issuesEl,
      h("div", { class: "hl-h" }, "Good"),
      goodEl,
      noDataEl,
      calHint
    );
    const lastSet = {};
    const row = (x) => h("div", { class: "hl-row", "data-key": x.key, "data-tone": x.tone, title: x.reason, text: x.issue || x.label });
    const chip = (x) => h("span", { class: "sys-chip", "data-key": x.key, "data-group": x.group, "data-tone": x.tone, title: x.reason }, h("span", { class: "dot" }), h("span", { class: "sys-l", text: x.label }));
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
      const pp = get("Position");
      const co = ctx.callouts && ctx.callouts();
      const items = systemsStatus({
        status: e ? e.f : null,
        statusState,
        gps: gpsFresh ? g.f : null,
        estimator: ev ? ev.f : null,
        attitude: at ? at.f : null,
        groundDistance: pp ? pp.f.ground_distance : null,
        calib: co ? co.log.calibState() : null,
        lastSet,
        now,
      });
      const sec = healthSections(items);
      issuesEl.replaceChildren(...(sec.issues.length ? sec.issues.map(row) : [h("div", { class: "hl-none", text: "No issues" })]));
      goodEl.replaceChildren(...sec.good.map(chip));
      noDataEl.replaceChildren(...(sec.noData.length ? [h("span", { class: "hl-nd-l", text: "No data" }), ...sec.noData.map(chip)] : []));
      noDataEl.hidden = !sec.noData.length;
      const s = systemsSummary(items);
      ctx.setTitle(!e && !g ? "Health" : s.bad ? `Health (${s.bad} failure${s.bad > 1 ? "s" : ""}${s.warn ? `, ${s.warn} warning${s.warn > 1 ? "s" : ""}` : ""})` : s.warn ? `Health (${s.warn} warning${s.warn > 1 ? "s" : ""})` : "Health (no issues)");
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
