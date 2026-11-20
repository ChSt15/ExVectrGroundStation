import { registerPanel } from "./registry.js";
import { createTelecommandPanel } from "../telecommand/telecommand_panel.js";

// TEMPORARY legacy telecommands (docs/telecommands.md), deleted with the flight software's T9b.
// SAFETY-RELEVANT: see telecommand/telecommand_panel.js.
registerPanel({
  id: "telecommands",
  title: "Commands (temporary)",
  consumes: ["VehicleStatus"],
  views: ["live"], // nothing is ever sent from the replay view
  create(el, ctx) {
    const inst = createTelecommandPanel(el, ctx);
    (window.exgs ??= {}).telecommands = inst;
    return inst;
  },
});
