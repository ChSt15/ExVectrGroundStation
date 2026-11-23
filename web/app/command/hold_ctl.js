// A button that fires only after an uninterrupted hold of `ms` (pointer, Space or Enter); a release, cancel, leave or
// blur cancels. A fill shows the progress. No dialog. Used by the header ABORT / FAILSAFE and the motors-arm button.
import { h } from "../ui.js";
import { HoldTimer } from "../radio/radio_logic.js";

export function holdControl({ label, title, ms, cls = "", testid, onDone }) {
  const fill = h("span", { class: "hold-fill" });
  const text = h("span", { class: "hold-label", text: label });
  const btn = h("button", { class: `hold-btn ${cls}`.trim(), title, "data-testid": testid }, fill, text);
  let anim = null;
  const stopAnim = () => { if (anim !== null) clearInterval(anim); anim = null; fill.style.width = "0"; };
  const timer = new HoldTimer(() => { stopAnim(); onDone(); }, undefined, ms);
  const stop = () => { timer.cancel(); stopAnim(); };
  const start = () => {
    if (btn.disabled || timer.holding) return;
    timer.start();
    anim = setInterval(() => { fill.style.width = `${Math.round(timer.progress() * 100)}%`; }, 30);
  };
  btn.addEventListener("pointerdown", (e) => {
    try { btn.setPointerCapture(e.pointerId); } catch (err) { /* synthetic pointer ids have no capture */ }
    start();
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave", "blur"]) btn.addEventListener(ev, stop);
  btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { start(); e.preventDefault(); } });
  btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stop(); });
  return { btn, stop, setLabel: (t) => { text.textContent = t; }, setDisabled: (d) => { btn.disabled = d; if (d) stop(); } };
}
