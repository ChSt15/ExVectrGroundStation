import { registerPanel } from "./registry.js";
import { createSimWindPanel } from "../telecommand/simwind_panel.js";

// TEMPORARY simulation wind telecommands (docs/telecommands.md), deleted with the flight software's T9b.
// SAFETY-RELEVANT: see telecommand/simwind_panel.js and simwind_logic.js.
registerPanel({
  id: "simwind",
  title: "Sim wind (temporary)",
  consumes: ["VehicleStatus"],
  inputs: true, // locked in the replay view
  create(el, ctx) {
    const inst = createSimWindPanel(el, ctx);
    (window.exgs ??= {}).simwind = inst;
    return inst;
  },
});
