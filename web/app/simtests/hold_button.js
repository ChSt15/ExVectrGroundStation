import { h } from "../ui.js";
import { HoldTimer, REQUEST_HOLD_MS } from "../requests/requests_logic.js";

/** A button that fires `onDone` only after a continuous 1 s hold (pointer or Space/Enter); progress bar inside. */
export function holdButton(label, title, onDone) {
  const bar = h("span", { class: "bar" });
  const btn = h("button", { class: "rq-btn", title }, h("span", { class: "rq-label", text: label }), bar);
  let anim = null;
  const stopAnim = () => { if (anim !== null) clearInterval(anim); anim = null; bar.style.width = "0"; };
  const timer = new HoldTimer(() => { stopAnim(); onDone(); }, undefined, REQUEST_HOLD_MS);
  const stop = () => { timer.cancel(); stopAnim(); };
  const start = () => {
    if (btn.disabled || timer.holding) return;
    timer.start();
    anim = setInterval(() => { bar.style.width = `${Math.round(timer.progress() * 100)}%`; }, 40);
  };
  btn.addEventListener("pointerdown", (e) => {
    try { btn.setPointerCapture(e.pointerId); } catch (err) { /* synthetic pointer ids have no capture */ }
    start();
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave", "blur"]) btn.addEventListener(ev, stop);
  btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { start(); e.preventDefault(); } });
  btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stop(); });
  return { btn, stop };
}
