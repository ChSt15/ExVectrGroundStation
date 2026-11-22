import { registerPanel } from "./registry.js";
import { createSimTestsPanel } from "../telecommand/simtests_panel.js";

// TEMPORARY simulation-only telecommands (docs/telecommands.md), deleted with the flight software's T9b.
// SAFETY-RELEVANT: see telecommand/simtests_panel.js. Replaces the former 'simwind' panel (now the Wind section).
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
