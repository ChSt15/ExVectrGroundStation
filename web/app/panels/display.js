// Display settings page (Operations dock): UI scale and 3D resolution, stored per browser (localStorage).
// Display only: nothing here is sent to the vehicle. docs/gui.md.
import { registerPanel } from "./registry.js";
import { h, section } from "../ui.js";
import { UI_SCALES, RES_SCALES, UI_LABELS, RES_LABELS, getDisplay, setDisplay, pixelRatio } from "../display_settings.js";

registerPanel({
  id: "display",
  title: "Display",
  consumes: [],
  create(el, ctx) {
    const sel = (testid, values, labels, key) => {
      const s = h("select", { "data-testid": testid }, ...values.map((v) => h("option", { value: v, text: labels[v] })));
      s.value = getDisplay()[key];
      s.addEventListener("change", () => {
        setDisplay({ [key]: s.value }, ctx.ls);
        info();
      });
      return s;
    };
    const ui = sel("disp-ui", UI_SCALES, UI_LABELS, "ui");
    const res = sel("disp-res", RES_SCALES, RES_LABELS, "res");
    const note = h("div", { class: "hint", "data-testid": "disp-info" });
    function info() {
      const d = getDisplay();
      note.textContent = `devicePixelRatio ${window.devicePixelRatio || 1}, 3D render ratio ${pixelRatio(window.devicePixelRatio, d.ui, d.res).toFixed(2)} (max 3)`;
    }
    const s = section(el, "This browser");
    s.append(
      h("div", { class: "co-row" }, h("span", { text: "UI scale" }), ui),
      h("div", { class: "co-row" }, h("span", { text: "3D resolution" }), res),
      note,
      h("div", { class: "hint", text: "Auto: the browser's own zoom and device pixel ratio. Raise UI scale if the GUI looks too small, 3D resolution if the 3D view looks blocky. Saved in this browser only." }),
    );
    info();
    window.addEventListener("exgs-display", info);
    return { destroy: () => window.removeEventListener("exgs-display", info) };
  },
});
