import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import { rad2deg } from "../fmt.js";

const FLAPS = ["tl", "tr", "bl", "br"];

registerPanel({
  id: "actuators",
  title: "Actuators",
  consumes: ["VehicleState", "FlapState", "TvcState"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const flagTone = (d) => (d.value === true ? "ok" : "");
    const s1 = section(el, "TVC");
    b.row(s1, "Thrust vector", { type: "VehicleState", field: "tvc_thrust", kind: "vec", unit: "N" });
    b.row(s1, "Torque", { type: "VehicleState", field: "tvc_torque", unit: "N·m" });
    b.row(s1, "Vane angles (xp xn yp yn)", { type: "TvcState", kind: "vec", unit: "deg", digits: 1, derive: (e) => e.f.vane?.map(rad2deg) });
    b.row(s1, "Motor commands (m1 m2)", { type: "TvcState", field: "motor", kind: "vec", digits: 2 });
    b.row(s1, "TVC enabled", { type: "TvcState", field: "enabled", kind: "bool", labels: ["no", "yes"] }, { tone: flagTone });
    b.row(s1, "Motors enabled", { type: "TvcState", field: "motors_enabled", kind: "bool", labels: ["no", "yes"] }, { tone: flagTone });
    const s2 = section(el, "Flaps (deg, 90 retracted, 0 extended)");
    const table = h("table", { class: "grid" }, h("tr", {}, h("th", { text: "" }), h("th", { text: "actual" }), h("th", { text: "commanded" })));
    FLAPS.forEach((name, i) => {
      table.append(
        h(
          "tr",
          {},
          h("th", { text: name.toUpperCase() }),
          h("td", {}, b.cell({ type: "FlapState", kind: "num", unit: "°", digits: 1, derive: (e) => rad2deg(e.f.angle?.[i]) })),
          h("td", {}, b.cell({ type: "FlapState", kind: "num", unit: "°", digits: 1, derive: (e) => rad2deg(e.f.cmd?.[i]) })),
        ),
      );
    });
    s2.append(table);
    b.row(s2, "Flaps enabled", { type: "FlapState", field: "enabled", kind: "bool", labels: ["no", "yes"] }, { tone: flagTone });
    const noFlaps = h("div", { class: "hint", text: "flap data: none (3D view shows the default 45°)" });
    s2.append(noFlaps);
    const update = () => {
      b.update();
      noFlaps.hidden = !!ctx.store.get(ctx.view(), "FlapState");
    };
    return { onTick: update, onViewChange: update, freshness: () => b.freshness() };
  },
});
