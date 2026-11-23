import { registerPanel } from "./registry.js";
import { createVehiclePanel } from "../vehicle/vehicle_panel.js";

// Vehicle settings that are requests with a result of their own (docs/requests.md): the stop altitude.
// SAFETY-RELEVANT: see vehicle/vehicle_panel.js.
registerPanel({
  id: "vehicle",
  title: "Vehicle",
  consumes: ["VehicleStatus", "MissionState"],
  inputs: true, // locked in the replay view: the controls are disabled and say why (nothing is ever sent)
  create(el, ctx) {
    const inst = createVehiclePanel(el, ctx);
    (window.exgs ??= {}).vehicle = inst;
    return inst;
  },
});
