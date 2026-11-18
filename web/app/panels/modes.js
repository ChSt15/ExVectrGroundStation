import { registerPanel } from "./registry.js";
import { Bindings, h } from "../ui.js";

const MODE_TONE = { Running: "ok", Ready: "ok", Startup: "info", SensorInit: "info", SensorCalib: "info", Failure: "bad" };

registerPanel({
  id: "modes",
  title: "Modes",
  consumes: ["VehicleState"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const big = b.cell(
      { type: "VehicleState", field: "mode", kind: "enum" },
      { class: "big", showSrc: true, tone: (d) => (d.state === "fresh" ? MODE_TONE[d.text] || "" : "") },
    );
    const flags = h("div", { class: "badges" });
    const armed = b.cell(
      { type: "VehicleState", field: "armed", kind: "bool", labels: ["disarmed", "ARMED"] },
      { class: "badge", tone: (d) => (d.value === true ? "warn" : "") },
    );
    const sim = b.cell(
      { type: "VehicleState", field: "sim", kind: "bool", labels: ["real", "SIM"] },
      { class: "badge", tone: (d) => (d.value === true ? "info" : "") },
    );
    flags.append(armed, sim);
    el.append(h("div", { class: "modes" }, big, flags));
    return {
      onTick: () => b.update(),
      onViewChange: () => b.update(),
      freshness: () => b.freshness(),
    };
  },
});
