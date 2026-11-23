import { registerPanel } from "./registry.js";
import { createSimTestsPanel } from "../simtests/simtests_panel.js";

// Simulation-only requests (docs/requests.md). SAFETY-RELEVANT: see simtests/simtests_panel.js.
registerPanel({
  id: "simtests",
  title: "Simulation testing",
  consumes: ["VehicleStatus"],
  inputs: true, // locked in the replay view
  create(el, ctx) {
    const inst = createSimTestsPanel(el, ctx);
    (window.exgs ??= {}).simtests = inst;
    return inst;
  },
});
