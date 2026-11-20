// Two stick gimbals (DISPLAY ONLY): a box per stick with a dot, the axis values and pulse widths underneath and the
// throttle position. Fed from stickState() (sticks.js); it never sends anything.
import { h } from "../ui.js";

const AXIS_NAME = { roll: "Roll", pitch: "Pitch", yaw: "Yaw", throttle: "Throttle" };

function fmtAxis(name, v, us) {
  if (v === undefined) return `${AXIS_NAME[name]} —`;
  const val = name === "throttle" ? `${Math.round(((v + 1) / 2) * 100)} %` : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2);
  return `${AXIS_NAME[name]} ${val}${us === undefined ? "" : ` · ${us} µs`}`;
}

export function createSticks(parent, { compact = false } = {}) {
  const caption = h("div", { class: "sticks-cap" });
  const make = (side) => {
    const dot = h("span", { class: "gdot" });
    const thr = h("span", { class: "gthr" });
    const box = h("div", { class: "gimbal", role: "img", "aria-label": `${side} stick` }, h("span", { class: "gx" }), h("span", { class: "gy" }), thr, dot);
    const lx = h("div", { class: "gval" });
    const ly = h("div", { class: "gval" });
    const wrap = h("div", { class: "gwrap", "data-side": side }, box, lx, ly);
    return { dot, box, lx, ly, wrap, thr };
  };
  const L = make("left");
  const R = make("right");
  const thrBig = h("div", { class: "thr-big", "data-testid": "throttle" });
  const root = h("div", { class: `sticks${compact ? " compact" : ""}`, "data-testid": "sticks" }, caption, h("div", { class: "sticks-row" }, L.wrap, thrBig, R.wrap));
  parent.append(root);

  function setG(g, s) {
    const known = s.x !== undefined && s.y !== undefined;
    g.dot.hidden = !known;
    if (known) {
      g.dot.style.left = `${50 + s.x * 46}%`;
      g.dot.style.top = `${50 - s.y * 46}%`;
    }
    const thrAxis = s.yLabel === "throttle" ? "y" : s.xLabel === "throttle" ? "x" : null;
    g.thr.hidden = thrAxis !== "y" || s.y === undefined;
    if (thrAxis === "y" && s.y !== undefined) g.thr.style.height = `${((s.y + 1) / 2) * 100}%`;
    g.lx.textContent = fmtAxis(s.xLabel, s.x, s.xUs);
    g.ly.textContent = fmtAxis(s.yLabel, s.y, s.yUs);
    g.box.dataset.x = s.x === undefined ? "" : s.x.toFixed(3);
    g.box.dataset.y = s.y === undefined ? "" : s.y.toFixed(3);
  }

  return {
    root,
    /** state: stickState() result or null; info: { caption, stale } */
    update(state, info = {}) {
      root.dataset.state = !state ? "missing" : info.stale ? "stale" : "fresh";
      caption.textContent = info.caption || "";
      if (!state) {
        for (const g of [L, R]) {
          g.dot.hidden = true;
          g.thr.hidden = true;
          g.lx.textContent = g.ly.textContent = "—";
        }
        thrBig.textContent = "throttle —";
        return;
      }
      setG(L, state.left);
      setG(R, state.right);
      thrBig.textContent = state.throttle === undefined ? "throttle —" : `throttle ${Math.round(state.throttle * 100)} %`;
      thrBig.dataset.low = String(state.throttle !== undefined && state.throttle < 0.02);
    },
  };
}
