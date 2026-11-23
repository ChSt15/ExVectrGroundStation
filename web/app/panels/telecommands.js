import { registerPanel } from "./registry.js";
import { createTelecommandPanel } from "../telecommand/telecommand_panel.js";

// TEMPORARY legacy telecommands (docs/telecommands.md), deleted with the flight software's T9b.
// SAFETY-RELEVANT: see telecommand/telecommand_panel.js.
registerPanel({
  id: "telecommands",
  title: "Commands (temporary)",
  consumes: ["VehicleStatus", "MissionState"],
  inputs: true, // locked in the replay view: the panel disables every button and says why (nothing is ever sent)
  create(el, ctx) {
    const inst = createTelecommandPanel(el, ctx);
    (window.exgs ??= {}).telecommands = inst;
    return inst;
  },
});
