// Vehicle group: failure bits as the vehicle reports them (VehicleStatus.failure_bits and failsafe), each with the
// time it was last seen set in this view. The status bar shows the current state; this panel keeps short
// failures visible after they cleared.
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { describe, fmtAge } from "../fmt.js";
import { FAILURE_BITS } from "../status/status_logic.js";

registerPanel({
  id: "failures",
  title: "Failures",
  consumes: ["VehicleStatus"],
  create(el, ctx) {
    const names = [...FAILURE_BITS.map((n, i) => ({ key: `b${i}`, label: n, test: (f) => (typeof f.failure_bits === "number" ? (f.failure_bits & (1 << i)) !== 0 : undefined) })), { key: "failsafe", label: "failsafe", test: (f) => f.failsafe }];
    const lastSet = {};
    const rows = names.map((n) => {
      const st = h("span", { class: "pill" });
      const note = h("span", { class: "note" });
      const r = h("div", { class: "frow", "data-key": n.key }, h("span", { class: "lbl", text: n.label }), st, note);
      el.append(r);
      return { n, st, note, r };
    });
    const reset = () => {
      for (const k of Object.keys(lastSet)) delete lastSet[k];
    };
    const render = () => {
      const v = ctx.view();
      const now = ctx.store.now(v);
      const e = ctx.store.get(v, "VehicleStatus");
      const d = describe({ type: "VehicleStatus", field: "failure_bits" }, (t, src) => ctx.store.get(v, t, src), now ?? Infinity, (t) => ctx.store.schema.get(t));
      let any = false;
      for (const x of rows) {
        const val = e ? x.n.test(e.f) : undefined;
        if (val === true) any = true;
        x.st.textContent = val === true ? "ACTIVE" : val === false ? "ok" : e ? "n/s" : "—";
        x.st.dataset.tone = d.state === "fresh" ? (val === true ? "bad" : val === false ? "ok" : "") : "";
        x.st.dataset.state = d.state;
        x.note.textContent = lastSet[x.n.key] !== undefined && now !== null ? `last set ${fmtAge(now - lastSet[x.n.key])} ago` : "";
      }
      ctx.setTitle(!e ? "Failures" : any ? "Failures (ACTIVE)" : "Failures (all clear)");
    };
    return {
      onPacket(pkt) {
        if (pkt.type !== "VehicleStatus") return;
        for (const x of rows) if (x.n.test(pkt.f) === true) lastSet[x.n.key] = pkt.t;
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
