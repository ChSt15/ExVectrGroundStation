import { registerPanel } from "./registry.js";
import { Bindings } from "../ui.js";

const vec3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number");

registerPanel({
  id: "guidance",
  title: "Guidance (target pose)",
  consumes: ["MissionState", "Position"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const M = "MissionState";
    b.row(el, "Target position", { type: M, field: "sp_pos", kind: "vec", unit: "m" });
    b.row(el, "Target velocity", { type: M, field: "sp_vel", kind: "vec", unit: "m/s" });
    const delta = ([m, p]) => (vec3(m.f.sp_pos) && vec3(p.f.pos) ? m.f.sp_pos.map((v, i) => v - p.f.pos[i]) : undefined);
    b.row(el, "To target (vector)", { types: [M, "Position"], kind: "vec", unit: "m", derive: delta });
    b.row(el, "Distance to target", {
      types: [M, "Position"],
      kind: "num",
      unit: "m",
      derive: (es) => {
        const d = delta(es);
        return d ? Math.hypot(...d) : undefined;
      },
    });
    const note = document.createElement("div");
    note.className = "hint";
    note.textContent = "Vector and distance are computed here for display only (target minus current position).";
    el.append(note);
    return { onTick: () => b.update(), onViewChange: () => b.update(), freshness: () => b.freshness() };
  },
});
