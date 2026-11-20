// Vehicle group: estimator health. Sensor states as the vehicle reports them (VehicleStatus.sensor_state, decoded
// for display; VehicleState.sensor_* where a link supplies them), estimator standard deviations (Estimator, USB debug
// set), attitude accuracies and ground distance.
import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import { decodeSensorState, hex } from "../status/status_logic.js";
import { describe, rad2deg } from "../fmt.js";

const TONE = { Ready: "ok", Init: "info", Calib: "info", Failure: "bad" };

registerPanel({
  id: "sensors",
  title: "Sensors & estimator",
  consumes: ["VehicleStatus", "Estimator", "Attitude", "Actuators", "VehicleState"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const chips = h("div", { class: "chips sensor-chips", "data-testid": "sensor-chips" });
    const chipInfo = h("div", { class: "hint" });
    el.append(chips, chipInfo);
    const D = 180 / Math.PI;
    b.vecTable(el, ["N", "W", "U"], [{ label: "Attitude σ", type: "Estimator", field: "att_std", unit: "°", digits: 2, scale: D }]);
    b.vecTable(el, ["x", "y", "z"], [
      { label: "Velocity σ", type: "Estimator", field: "vel_std", unit: "m/s", digits: 2 },
      { label: "Position σ", type: "Estimator", field: "pos_std", unit: "m", digits: 2 },
    ]);
    const s = section(el, "");
    b.row(s, "Attitude acc. tilt / north", { type: "Attitude", kind: "vec", unit: "°", digits: 2, derive: (e) => (e.f.tilt_acc === undefined && e.f.north_acc === undefined ? undefined : [rad2deg(e.f.tilt_acc ?? null), rad2deg(e.f.north_acc ?? null)]) });
    b.row(s, "Ground distance", { type: "Actuators", field: "ground_distance", unit: "m", digits: 2 });
    el.append(h("div", { class: "hint", text: "σ: 1 sigma of the estimator; attitude about the world axes North, West, Up." }));
    let chipKey = "";
    const renderChips = () => {
      const v = ctx.view();
      const now = ctx.store.now(v) ?? Infinity;
      const d = describe({ type: "VehicleStatus", field: "sensor_state" }, (t, src) => ctx.store.get(v, t, src), now, (t) => ctx.store.schema.get(t));
      const dec = d.state === "fresh" || d.state === "stale" ? decodeSensorState(d.value) : [];
      const key = `${d.state}|${dec.map((x) => x.state).join(",")}`;
      chipInfo.textContent = d.state === "missing" ? "sensor state: no VehicleStatus received" : d.state === "not supplied" ? "sensor state: not supplied" : `sensor_state ${hex(d.value, 3)}${d.state === "stale" ? " (stale)" : ""}`;
      if (key === chipKey) return;
      chipKey = key;
      chips.replaceChildren(...dec.map((x) => h("span", { class: "pill", "data-tone": d.state === "fresh" ? TONE[x.state] || "" : "", "data-state": d.state }, h("b", { text: x.name }), ` ${x.state}`)));
    };
    const update = () => {
      b.update();
      renderChips();
    };
    return { onTick: update, onViewChange: update, freshness: () => b.freshness() };
  },
});
