// Target vs current panel (Bench, Mission column, compact): target (MissionState setpoints) against the current estimate (Position), and the error
// target minus current (display-only arithmetic on vehicle-reported values). The vehicle sends no attitude target.
import { registerPanel } from "./registry.js";
import { Bindings, h, tiles } from "../ui.js";

const M = "MissionState";
const P = "Position";
const v3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number");
const diff = (a, b) => (v3(a) && v3(b) ? a.map((x, i) => x - b[i]) : undefined);
const err = (tf, cf) => ([m, p]) => (m.f[tf] === undefined || p.f[cf] === undefined ? undefined : diff(m.f[tf], p.f[cf]) ?? null);

registerPanel({
  id: "targetvs",
  title: "Target vs current",
  consumes: [M, P],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const compact = !!(ctx.options && ctx.options.compact);
    const t = tiles(el, "t3");
    b.tile(t, "Distance to target", { types: [M, P], kind: "num", unit: "m", digits: 2, derive: (es) => { const d = err("sp_pos", "pos")(es); return d ? Math.hypot(...d) : d; } });
    b.tile(t, "Altitude error", { types: [M, P], kind: "num", unit: "m", digits: 2, derive: (es) => { const d = err("sp_pos", "pos")(es); return d ? d[2] : d; } });
    b.tile(t, "Speed error", { types: [M, P], kind: "num", unit: "m/s", digits: 2, derive: (es) => { const d = err("sp_vel", "vel")(es); return d ? Math.hypot(...d) : d; } });
    const rows = compact
      ? [
          { label: "Position error", types: [M, P], vec: err("sp_pos", "pos"), unit: "m" },
          { label: "Velocity error", types: [M, P], vec: err("sp_vel", "vel"), unit: "m/s" },
        ]
      : [
          { label: "Target position", type: M, field: "sp_pos", unit: "m" },
          { label: "Position", type: P, field: "pos", unit: "m" },
          { label: "Error", key: "err_pos", types: [M, P], vec: err("sp_pos", "pos"), unit: "m" },
          { label: "Target velocity", type: M, field: "sp_vel", unit: "m/s" },
          { label: "Velocity", type: P, field: "vel", unit: "m/s" },
          { label: "Error", key: "err_vel", types: [M, P], vec: err("sp_vel", "vel"), unit: "m/s" },
        ];
    b.vecTable(el, ["N", "W", "U"], rows);
    el.append(h("div", { class: "hint", text: compact ? "Error = target − current (display only). Attitude target: not in the telemetry." : "Error = target − current, computed here for display only. The vehicle sends no attitude or rate target (see docs/gui.md, missing telemetry)." }));
    return { onTick: () => b.update(), onViewChange: () => b.update(), freshness: () => b.freshness() };
  },
});
