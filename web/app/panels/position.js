// Vehicle group: position and velocity estimate (NWU world frame), GPS. Vehicle-reported values only.
import { registerPanel } from "./registry.js";
import { Bindings, h, section, tiles } from "../ui.js";
import { rad2deg } from "../fmt.js";
import { groundDistanceView } from "../status/ground_distance.js";

const P = "Position";
const G = "Gps";
const comp = (field, i) => (e) => (Array.isArray(e.f[field]) ? e.f[field][i] ?? null : e.f[field] === null ? null : undefined);

registerPanel({
  id: "position",
  title: "Position & velocity",
  consumes: [P, G, "Actuators"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const compact = !!(ctx.options && ctx.options.compact);
    const t = tiles(el, "t4"); // four tiles in one row: the panel keeps its height
    b.tile(t, "Altitude (Up)", { type: P, kind: "num", unit: "m", digits: 2, derive: comp("pos", 2) });
    b.tile(t, "Vertical speed", { type: P, kind: "num", unit: "m/s", digits: 2, derive: comp("vel", 2) });
    b.tile(t, "Horizontal speed", {
      type: P,
      kind: "num",
      unit: "m/s",
      digits: 2,
      derive: (e) => (Array.isArray(e.f.vel) && typeof e.f.vel[0] === "number" && typeof e.f.vel[1] === "number" ? Math.hypot(e.f.vel[0], e.f.vel[1]) : undefined),
    });
    // downward lidar: the vehicle's estimate of the distance to the ground, valid only in the sensor's useful range
    const gd = h("div", { class: "tile gd", "data-testid": "ground-distance", title: "Downward lidar (Actuators.ground_distance): valid only in the sensor's useful range" }, h("div", { class: "tile-l", text: "Ground distance" }), h("span", { class: "val big" }, h("span", { class: "v" }), h("span", { class: "unit" })));
    t.append(gd);
    const gdUpdate = () => {
      const { store, view } = ctx;
      const now = store.now(view());
      const d = groundDistanceView(store.get(view(), "Actuators"), now === null ? NaN : now);
      gd.dataset.state = d.state;
      gd.querySelector(".v").textContent = d.text;
      gd.querySelector(".unit").textContent = d.unit ? ` ${d.unit}` : "";
      gd.querySelector(".val").style.color = d.state === "ok" ? "" : "var(--muted, #888)";
    };
    b.vecTable(el, ["N", "W", "U"], [
      { label: "Position", type: P, field: "pos", unit: "m" },
      { label: "Velocity", type: P, field: "vel", unit: "m/s" },
    ]);
    if (!compact) {
      const s = section(el, "");
      b.row(s, "H / V accuracy", { type: P, kind: "vec", derive: (e) => (e.f.h_acc === undefined && e.f.v_acc === undefined ? undefined : [e.f.h_acc ?? null, e.f.v_acc ?? null]) }, { title: "legacy value without a physical unit" });
      const g = section(el, "GPS");
      b.row(g, "Lat / Lon", { type: G, kind: "vec", unit: "°", digits: 6, derive: (e) => (e.f.lat === undefined || e.f.lon === undefined ? undefined : [rad2deg(e.f.lat), rad2deg(e.f.lon)]) });
      b.row(g, "Altitude", { type: G, field: "alt", unit: "m", digits: 1 });
      b.row(g, "Satellites / fix", { type: G, kind: "vec", digits: 0, derive: (e) => (e.f.num_sats === undefined ? undefined : [e.f.num_sats, e.f.fix_type ?? null]) });
      b.row(g, "Accuracy pos / alt / vel", { type: G, kind: "vec", digits: 2, derive: (e) => (e.f.pos_acc === undefined ? undefined : [e.f.pos_acc, e.f.alt_acc ?? null, e.f.vel_acc ?? null]) });
      el.append(h("div", { class: "hint", text: "World frame NWU: X North, Y West, Z Up, origin at the position reference." }));
    }
    return { onTick: () => (b.update(), gdUpdate()), onViewChange: () => (b.update(), gdUpdate()), freshness: () => b.freshness() };
  },
});
