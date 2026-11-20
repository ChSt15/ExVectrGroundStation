import { registerPanel } from "./registry.js";
import { createUpdatePanel } from "../update/update_panel.js";

// Self-update (docs/updating.md): version, check, 'Update and restart' (hold 1 s). Not a vehicle uplink; the core
// refuses while armed, motors enabled, mission running, recording, RC lease held or a request in flight.
registerPanel({
  id: "update",
  title: "Software",
  consumes: ["VehicleStatus", "MissionState"],
  inputs: true, // locked in the replay view
  create(el, ctx) {
    const inst = createUpdatePanel(el, ctx);
    (window.exgs ??= {}).update = inst;
    return inst;
  },
});
