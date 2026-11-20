// Vehicle group: attitude estimate (vehicle-reported Attitude packet; roll/pitch/yaw and tilt are display-only
// arithmetic on the reported quaternion). Angles in degrees, rates in deg/s.
import { registerPanel } from "./registry.js";
import { Bindings, h, section, tiles } from "../ui.js";
import { quatToRpy, rad2deg, tiltFromVertical } from "../fmt.js";

const A = "Attitude";
const rpy = (i) => (e) => {
  const r = quatToRpy(e.f.q);
  return r ? rad2deg(r[i]) : e.f.q === null ? null : undefined;
};

registerPanel({
  id: "attitude",
  title: "Attitude",
  consumes: [A],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const compact = !!(ctx.options && ctx.options.compact);
    const t = tiles(el, "t4");
    b.tile(t, "Roll", { type: A, kind: "num", unit: "°", digits: 1, derive: rpy(0) });
    b.tile(t, "Pitch", { type: A, kind: "num", unit: "°", digits: 1, derive: rpy(1) });
    b.tile(t, "Yaw", { type: A, kind: "num", unit: "°", digits: 1, derive: rpy(2) });
    b.tile(t, "Tilt", { type: A, kind: "num", unit: "°", digits: 1, derive: (e) => rad2deg(tiltFromVertical(e.f.q)) });
    b.vecTable(el, ["x", "y", "z"], [{ label: "Rate (body)", type: A, field: "omega", unit: "°/s", digits: 1, scale: 180 / Math.PI }]);
    if (!compact) {
      const s = section(el, "");
      b.row(s, "Quaternion w x y z", { type: A, field: "q", kind: "vec", digits: 3 });
      b.row(s, "Accuracy tilt / north", { type: A, kind: "vec", unit: "°", digits: 2, derive: (e) => (e.f.tilt_acc === undefined && e.f.north_acc === undefined ? undefined : [rad2deg(e.f.tilt_acc ?? null), rad2deg(e.f.north_acc ?? null)]) });
      el.append(h("div", { class: "hint", text: "Roll, pitch, yaw: ZYX angles of q (body to world, NWU), display only. Tilt: body +Z to world +Z." }));
    }
    return { onTick: () => b.update(), onViewChange: () => b.update(), freshness: () => b.freshness() };
  },
});
