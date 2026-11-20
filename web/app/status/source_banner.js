// Data-source banner (docs/gui.md): one loud, colour-coded strip on top of every page saying whether the data is
// REAL, SIMULATION or a REPLAY (with log name, position and what the recording was), and whether inputs are locked.
// In the replay view it also carries the replay transport (the `transport` panel is mounted into its slot).
import { h } from "../ui.js";
import { describeSource, simulationFlag } from "./source_logic.js";

export const BANNER_CONSUMES = ["VehicleStatus"];

export function createSourceBanner(ctx) {
  const label = h("span", { class: "srcb-label", "data-testid": "source-label" });
  const details = h("span", { class: "srcb-details" });
  const recorded = h("span", { class: "srcb-recorded", "data-testid": "source-recorded" });
  const lock = h("span", { class: "srcb-lock", "data-testid": "source-lock" });
  const transportSlot = h("div", { class: "srcb-transport" });
  const el = h("div", { id: "sourcebanner", class: "srcb", role: "status", "aria-live": "polite", "data-testid": "source-banner" }, label, details, recorded, transportSlot, h("span", { class: "grow" }), lock);
  let current = null;

  function render() {
    const schema = ctx.store.schema.get("VehicleStatus");
    const nowLive = ctx.store.now("live") ?? Infinity;
    const nowReplay = ctx.store.now("replay") ?? Infinity;
    const st = ctx.status();
    const d = describeSource({
      connState: ctx.connState(),
      view: ctx.view(),
      live: simulationFlag(ctx.store.get("live", "VehicleStatus"), nowLive, schema),
      recorded: simulationFlag(ctx.store.get("replay", "VehicleStatus"), nowReplay, schema),
      replay: st && st.replay,
    });
    current = d;
    el.dataset.kind = d.kind;
    label.textContent = d.label;
    details.textContent = d.details.join(" · ");
    recorded.hidden = !d.recorded;
    recorded.textContent = d.recorded ? `recorded: ${d.recorded}` : "";
    recorded.dataset.rec = d.recorded || "";
    lock.hidden = !d.inputsLocked;
    lock.textContent = d.inputsLocked ? `🔒 ${d.lockReason}` : "";
    transportSlot.hidden = d.kind !== "replay";
  }
  return { el, render, transportSlot, state: () => current };
}
