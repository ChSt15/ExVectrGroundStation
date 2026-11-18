import { registerPanel } from "./registry.js";
import { Bindings, h } from "../ui.js";
import { fmtAge } from "../fmt.js";

const FLAGS = [
  ["fail_sensor", "Sensor"],
  ["fail_attitude", "Attitude"],
  ["fail_position", "Position"],
  ["fail_pos_oob", "Position out of bounds"],
  ["fail_att_oob", "Attitude out of bounds"],
  ["fail_radio_loss", "Radio loss"],
];

registerPanel({
  id: "failures",
  title: "Failures",
  consumes: ["VehicleState"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const lastSet = {}; // flag -> view time (s) when a received VehicleState last had it set; this view only
    const seen = {};
    const cells = {};
    for (const [flag, label] of FLAGS) {
      const cell = b.row(el, label, { type: "VehicleState", field: flag, kind: "bool", labels: ["ok", "FAILED"] }, {
        tone: (d) => (d.value === true ? "bad" : d.value === false ? "ok" : ""),
      });
      const note = h("span", { class: "note" });
      cell.parentElement.append(note);
      cells[flag] = note;
    }
    const reset = () => {
      for (const [flag] of FLAGS) {
        delete lastSet[flag];
        delete seen[flag];
      }
    };
    const render = () => {
      b.update();
      const now = ctx.store.now(ctx.view());
      for (const [flag] of FLAGS) {
        cells[flag].textContent = lastSet[flag] !== undefined && now !== null ? `last set ${fmtAge(now - lastSet[flag])} ago` : "";
      }
      const entry = ctx.store.get(ctx.view(), "VehicleState");
      const any = entry && FLAGS.some(([f]) => entry.f[f] === true);
      const known = entry && FLAGS.some(([f]) => entry.f[f] !== undefined);
      ctx.setTitle(!known ? "Failures" : any ? "Failures (ACTIVE)" : "Failures (all clear)");
    };
    return {
      onPacket(pkt) {
        if (pkt.type !== "VehicleState") return;
        for (const [flag] of FLAGS) if (pkt.f[flag] === true) lastSet[flag] = pkt.t;
      },
      onReset: reset,
      onViewChange() {
        reset();
        const e = ctx.store.get(ctx.view(), "VehicleState");
        if (e) for (const [flag] of FLAGS) if (e.f[flag] === true) lastSet[flag] = e.t;
        render();
      },
      onTick: render,
      freshness: () => b.freshness(),
    };
  },
});
