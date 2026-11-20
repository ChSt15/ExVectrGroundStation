// Vehicle group: actuators as the vehicle reports them. `Actuators` is the vehicle's commanded output (flaps, TVC,
// thrust), `FlapState` the flap angles (and commands if supplied), `TvcState` / `VehicleState.tvc_thrust` come only
// from links that carry them (sections without data are hidden with a note). Angles in degrees.
import { registerPanel } from "./registry.js";
import { Bindings, h, section, tiles } from "../ui.js";
import { rad2deg } from "../fmt.js";

const FLAPS = ["TL", "TR", "BL", "BR"];
const D = 180 / Math.PI;

registerPanel({
  id: "actuators",
  title: "Actuators",
  consumes: ["Actuators", "FlapState", "TvcState", "VehicleState"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const compact = !!(ctx.options && ctx.options.compact);
    const t = tiles(el, "t3");
    b.tile(t, "Thrust", { type: "Actuators", field: "thrust", unit: "N", digits: 2 });
    b.tile(t, "TVC x", { type: "Actuators", kind: "num", unit: "°", digits: 1, derive: (e) => (Array.isArray(e.f.tvc) ? rad2deg(e.f.tvc[0] ?? null) : undefined) });
    b.tile(t, "TVC y", { type: "Actuators", kind: "num", unit: "°", digits: 1, derive: (e) => (Array.isArray(e.f.tvc) ? rad2deg(e.f.tvc[1] ?? null) : undefined) });
    b.tile(t, "Roll torque", { type: "Actuators", field: "roll_torque", unit: "N·m", digits: 3 });
    const rows = [
      { label: "Command", key: "cmd_act", type: "Actuators", field: "flaps", unit: "°", digits: 1, scale: D },
      { label: "Angle", key: "angle", type: "FlapState", field: "angle", unit: "°", digits: 1, scale: D },
    ];
    if (!compact) rows.push({ label: "Command (FlapState)", key: "cmd_flap", type: "FlapState", field: "cmd", unit: "°", digits: 1, scale: D });
    const flapTable = b.vecTable(el, FLAPS, rows);
    flapTable.querySelector("thead th").textContent = "Flaps";
    const noFlaps = h("div", { class: "hint", "data-testid": "noflaps", text: "flap data: none (3D view shows the default 45°)" });
    el.append(noFlaps);
    let tvcSect = null;
    let tvcNote = null;
    if (!compact) {
      el.append(h("div", { class: "hint", text: "Flap 0° = extended, 90° = folded. Command = the vehicle's output, not what the ground station sent." }));
      tvcSect = section(el, "TVC state (TvcState, VehicleState)");
      b.row(tvcSect, "Vanes xp xn yp yn", { type: "TvcState", kind: "vec", unit: "°", digits: 1, derive: (e) => e.f.vane?.map(rad2deg) });
      b.row(tvcSect, "Motors m1 m2", { type: "TvcState", field: "motor", kind: "vec", digits: 2 });
      b.row(tvcSect, "TVC / motors enabled", { type: "TvcState", kind: "vec", derive: (e) => (e.f.enabled === undefined ? undefined : [e.f.enabled ? "yes" : "no", e.f.motors_enabled ? "yes" : "no"]), format: (v) => v.join(" / ") });
      b.row(tvcSect, "Thrust vector (body)", { type: "VehicleState", field: "tvc_thrust", kind: "vec", unit: "N" });
      b.row(tvcSect, "Torque", { type: "VehicleState", field: "tvc_torque", unit: "N·m" });
      tvcNote = h("div", { class: "hint", text: "TvcState: not received on this link" });
      el.append(tvcNote);
    }
    const update = () => {
      b.update();
      const v = ctx.view();
      noFlaps.hidden = !!ctx.store.get(v, "FlapState");
      const fs = ctx.store.get(v, "FlapState");
      const cmdRow = flapTable.querySelector("tr[data-row=cmd_flap]");
      if (cmdRow) cmdRow.hidden = !fs || fs.f.cmd === undefined;
      if (tvcSect) {
        const has = !!ctx.store.get(v, "TvcState");
        tvcSect.hidden = !has;
        tvcNote.hidden = has;
      }
    };
    return { onTick: update, onViewChange: update, freshness: () => b.freshness() };
  },
});
