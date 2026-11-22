// 'Simulation testing' page (docs/telecommands.md): hosts the registered sections (wind, servo faults, later more).
// TEMPORARY, SAFETY-RELEVANT UI like the telecommand path. Everything is greyed out and labelled 'simulation mode
// only' unless the VEHICLE reports simulation mode (display gating; the vehicle refuses outside it anyway).
import { h } from "../ui.js";
import { simSections } from "./simtests_registry.js";
import { SIM_ONLY_LABEL, simTestState } from "./simtests_logic.js";
import { commandPathRows } from "../status/command_path.js";
import { renderCommandPath } from "../status/command_path_view.js";
import { reportedSimulation } from "./telecommand_logic.js";
import "./simwind_section.js";
import "./simfaults_section.js";

export function createSimTestsPanel(el, ctx) {
  const banner = h("div", { class: "tc-sim", "data-testid": "st-sim" });
  const why = h("div", { class: "rq-msg st-why", "data-testid": "st-why", role: "status" });
  const root = h("div", { class: "st-root" });
  const pathBox = h("div", { class: "cp-box", "data-testid": "st-path" });
  el.append(h("div", { class: "tc-warn", text: `Simulation tests are ${SIM_ONLY_LABEL}. They are enabled only while the vehicle itself reports simulation mode, in the live view, on a control host with a route.` }),
    h("div", { class: "tc-warn", "data-testid": "st-sc-note", text: "Simulation is entered with the virtual radio's SC switch (Virtual radio panel, hold master): the vehicle boots REAL, and only an observed SC change 0 -> 1 -> 0 (or 1 -> 0) while on the ground and not committed changes the mode. There is no simulation command any more. The vehicle reports the result below." }),
    banner, pathBox, why, root);
  const mounted = simSections().map((def) => {
    const box = h("section", { class: "st-section", "data-section": def.id });
    const tag = h("span", { class: "st-tag", text: SIM_ONLY_LABEL });
    root.append(h("h3", { class: "st-title" }, def.title, " ", tag), box);
    return { def, box, inst: def.create(box, ctx) };
  });
  const each = (fn) => { for (const m of mounted) fn(m.inst); };

  function render() {
    const canControl = !!(ctx.api.welcome && ctx.api.welcome.can_control);
    const st = ctx.status();
    const entry = ctx.store.get(ctx.view(), "VehicleStatus");
    const s = simTestState({
      apiState: ctx.api.state, view: ctx.view(), canControl, route: st ? st.telecommand_route || null : null,
      simulation: entry && entry.f ? entry.f.simulation : null,
    });
    const rep = reportedSimulation(entry);
    banner.textContent = rep.text;
    banner.dataset.tone = rep.tone;
    renderCommandPath(pathBox, commandPathRows(entry));
    why.textContent = s.enabled ? "" : `Disabled (${SIM_ONLY_LABEL}): ${s.reason}`;
    root.dataset.enabled = String(s.enabled);
    for (const m of mounted) m.box.classList.toggle("st-off", !s.enabled);
  }

  return {
    onReady() { each((i) => i.onReady?.()); render(); },
    onTelecommands(m) { each((i) => i.onTelecommands?.(m)); },
    onStatus(x) { each((i) => i.onStatus?.(x)); render(); },
    onTick() { each((i) => i.onTick?.()); render(); },
    onViewChange() { each((i) => i.onViewChange?.()); render(); },
    destroy() { each((i) => i.destroy?.()); },
    sections: () => mounted.map((m) => m.def.id),
  };
}
