// DOM for commandPathRows (command_path.js): a small label/value list, replaced on every render.
import { h } from "../ui.js";

export function renderCommandPath(box, rows) {
  box.replaceChildren(
    ...(rows.length
      ? rows.map((r) => h("div", { class: "cp-row", "data-key": r.key, "data-tone": r.tone }, h("span", { class: "cp-l", text: r.label }), h("span", { class: "cp-v", text: r.text })))
      : [h("div", { class: "cp-row", "data-tone": "dim", text: "No VehicleStatus received: the vehicle's command path state is unknown." })]),
  );
}
