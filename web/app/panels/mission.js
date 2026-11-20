// Mission group: mission state, phase and timing as the vehicle reports them (MissionState), the current target
// point, and the phase changes seen in this view. The telemetry carries the current setpoint only: no list of
// upcoming points (docs/gui.md, missing telemetry).
import { registerPanel } from "./registry.js";
import { Bindings, h, section, tiles } from "../ui.js";
import { fmtMissionTime } from "../fmt.js";

const M = "MissionState";
const MAX_LOG = 8;

registerPanel({
  id: "mission",
  title: "Mission",
  consumes: [M],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const compact = !!(ctx.options && ctx.options.compact);
    const hero = tiles(el, "t1 hero");
    b.tile(hero, "Phase", { type: M, field: "mission_phase", kind: "enum" }, { class: "xl" });
    const t = tiles(el, "t2");
    b.tile(t, "Mission time", { type: M, field: "t_mission", kind: "time", format: (v) => fmtMissionTime(v).replace(/\.(\d)\d s$/, ".$1 s") });
    b.tile(t, "Mission", { type: M, field: "mission_mode", kind: "enum" });
    const s = section(el, "Current target point");
    b.vecTable(s, ["N", "W", "U"], [
      { label: "Position", type: M, field: "sp_pos", unit: "m" },
      ...(compact ? [] : [{ label: "Velocity", type: M, field: "sp_vel", unit: "m/s" }]),
    ]);
    b.row(s, "Mission index", { type: M, field: "mission_index", kind: "num", digits: 0 });
    el.append(h("div", { class: "hint", text: "Upcoming points: not in the telemetry (only the current setpoint is sent)." }));
    const logSect = section(el, "Phase changes (this view)");
    const log = h("ol", { class: "phaselog", "data-testid": "phase-log" });
    logSect.append(log);
    let entries = [];
    let lastPhase;
    const renderLog = () => {
      log.replaceChildren(...(entries.length ? entries.slice(-MAX_LOG).reverse().map((e) => h("li", {}, h("span", { class: "mono", text: e.tm }), " ", h("b", { text: e.phase }))) : [h("li", { class: "dim", text: "none yet" })]));
    };
    const reset = () => {
      entries = [];
      lastPhase = undefined;
      renderLog();
    };
    if (compact) logSect.hidden = true;
    renderLog();
    return {
      onPacket(pkt) {
        if (pkt.type !== M || pkt.f.mission_phase === undefined) return;
        if (lastPhase !== undefined && pkt.f.mission_phase !== lastPhase) {
          entries.push({ phase: `${lastPhase} → ${pkt.f.mission_phase}`, tm: pkt.f.t_mission === undefined ? "" : fmtMissionTime(pkt.f.t_mission) });
          if (entries.length > 50) entries.shift();
          renderLog();
        }
        lastPhase = pkt.f.mission_phase;
      },
      onReset: reset,
      onViewChange() {
        reset();
        b.update();
      },
      onTick: () => b.update(),
      freshness: () => b.freshness(),
    };
  },
});
