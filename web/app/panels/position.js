import { registerPanel } from "./registry.js";
import { Bindings, section } from "../ui.js";
import { quatToRpy, tiltFromVertical, rad2deg } from "../fmt.js";

const AXES = "NWU";

registerPanel({
  id: "position",
  title: "Position & orientation",
  consumes: ["Position", "Attitude", "Gps"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const P = "Position";
    const A = "Attitude";
    const G = "Gps";
    const s1 = section(el, `Position, velocity (world NWU: X ${AXES[0]}, Y ${AXES[1]}, Z ${AXES[2]})`);
    b.row(s1, "Position", { type: P, field: "pos", kind: "vec", unit: "m" });
    b.row(s1, "Velocity", { type: P, field: "vel", kind: "vec", unit: "m/s" });
    b.row(s1, "H / V accuracy", { type: P, kind: "vec", derive: (e) => (e.f.h_acc === undefined && e.f.v_acc === undefined ? undefined : [e.f.h_acc ?? null, e.f.v_acc ?? null]) });
    const s2 = section(el, "Orientation (q = w x y z, body to world)");
    b.row(s2, "Quaternion", { type: A, field: "q", kind: "vec", digits: 3 });
    b.row(s2, "Roll Pitch Yaw", { type: A, kind: "vec", unit: "deg", digits: 1, derive: (e) => quatToRpy(e.f.q)?.map(rad2deg) });
    b.row(s2, "Tilt from vertical", { type: A, kind: "num", unit: "deg", digits: 1, derive: (e) => rad2deg(tiltFromVertical(e.f.q)) });
    b.row(s2, "Angular rate", { type: A, field: "omega", kind: "vec", unit: "rad/s" });
    const note = document.createElement("div");
    note.className = "hint";
    note.textContent = "Roll, pitch, yaw: ZYX convention, display only. Tilt: angle of body +Z to world +Z.";
    s2.append(note);
    const s3 = section(el, "GPS");
    b.row(s3, "Lat / Lon", { type: G, kind: "vec", unit: "deg", digits: 6, derive: (e) => (e.f.lat === undefined || e.f.lon === undefined ? undefined : [rad2deg(e.f.lat), rad2deg(e.f.lon)]) });
    b.row(s3, "Altitude", { type: G, field: "alt", unit: "m" });
    b.row(s3, "Satellites", { type: G, field: "num_sats", digits: 0 });
    b.row(s3, "Position accuracy", { type: G, field: "pos_acc", unit: "m" });
    return { onTick: () => b.update(), onViewChange: () => b.update(), freshness: () => b.freshness() };
  },
});
