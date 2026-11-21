// 'Wind' section of the Simulation testing page: the former 'Sim wind (temporary)' panel, behaviour unchanged.
import { registerSimSection } from "./simtests_registry.js";
import { createSimWindPanel } from "./simwind_panel.js";

registerSimSection({
  id: "wind",
  title: "Wind",
  order: 10,
  create(el, ctx) {
    const inst = createSimWindPanel(el, ctx);
    (window.exgs ??= {}).simwind = inst;
    return inst;
  },
});
