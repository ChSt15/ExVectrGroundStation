import { registerPanel } from "./registry.js";
import { Bindings } from "../ui.js";

registerPanel({
  id: "mission",
  title: "Mission state",
  consumes: ["MissionState"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const T = "MissionState";
    b.row(el, "Mission mode", { type: T, field: "mission_mode", kind: "enum" });
    b.row(el, "Phase", { type: T, field: "mission_phase", kind: "enum" }, { class: "strong" });
    b.row(el, "Index", { type: T, field: "mission_index", kind: "num", digits: 0 });
    b.row(el, "Mission time", { type: T, field: "t_mission", kind: "time" }, { class: "strong" });
    return { onTick: () => b.update(), onViewChange: () => b.update(), freshness: () => b.freshness() };
  },
});
