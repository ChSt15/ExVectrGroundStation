// Data-source tag (docs/gui.md): a small colour-coded pill at the start of the vehicle status bar row saying REAL,
// SIMULATION, REPLAY (or unknown / stale / no core), and in the replay view a compact strip under the status bar with
// the log name, position, what the recording was, the transport controls and "replay: inputs locked".
import { h } from "../ui.js";
import { describeSource, simulationFlag } from "./source_logic.js";

export const BANNER_CONSUMES = ["VehicleStatus"];

export function createSourceBanner(ctx) {
  const el = h("span", { id: "sourcetag", class: "srctag", role: "status", "aria-live": "polite", "data-testid": "source-banner" });
  const details = h("span", { class: "rs-details mono", "data-testid": "source-details" });
  const recorded = h("span", { class: "rs-rec", "data-testid": "source-recorded" });
  const lock = h("span", { class: "rs-lock", "data-testid": "source-lock" });
  const transportSlot = h("div", { class: "rs-transport" });
  const strip = h("div", { class: "replaystrip", "data-testid": "replay-strip", hidden: true }, h("span", { class: "rs-title", text: "REPLAY" }), details, recorded, transportSlot, lock);
  let current = null;

  function render() {
    const schema = ctx.store.schema.get("VehicleStatus");
    const st = ctx.status();
    const d = describeSource({
      connState: ctx.connState(),
      view: ctx.view(),
      live: simulationFlag(ctx.store.get("live", "VehicleStatus"), ctx.store.now("live") ?? Infinity, schema),
      recorded: simulationFlag(ctx.store.get("replay", "VehicleStatus"), ctx.store.now("replay") ?? Infinity, schema),
      replay: st && st.replay,
    });
    current = d;
    el.dataset.kind = d.kind;
    el.textContent = d.short;
    el.title = [d.label, ...d.details, d.inputsLocked ? d.lockReason : ""].filter(Boolean).join(" · ");
    strip.hidden = d.kind !== "replay";
    details.textContent = d.details.join(" · ");
    recorded.hidden = !d.recorded;
    recorded.textContent = d.recorded ? `recorded: ${d.recorded}` : "";
    recorded.dataset.rec = d.recorded || "";
    lock.textContent = d.inputsLocked ? `🔒 ${d.lockReason}` : "";
  }
  return { el, strip, render, transportSlot, state: () => current };
}
