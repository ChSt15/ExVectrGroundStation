// Mission group: mission state, phase and timing as the vehicle reports them (MissionState), the current target
// point, and the phase changes seen in this view. The telemetry carries the current setpoint only: no list of
// upcoming points (docs/gui.md, missing telemetry).
import { registerPanel } from "./registry.js";
import { Bindings, h, section, tiles } from "../ui.js";
import { fmtMissionTime } from "../fmt.js";
import { MissionTimeFeed } from "../status/mission_time_display.js";

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
    const t = tiles(el, "t3");
    const one = (v) => fmtMissionTime(v).replace(/\.(\d)\d s$/, ".$1 s");
    const feed = new MissionTimeFeed();
    // Continuous mission clock (T- countdown, T0, then over all phases); an em dash until a mission has been seen.
    b.tile(t, "Mission time", { type: M, kind: "time", derive: (e) => feed.missionTime(e), format: one }, { key: "mission-time" });
    b.tile(t, "Phase time", { type: M, field: "t_mission", kind: "time", format: one }, { class: "small", tileClass: "tile-small", key: "phase-time" });
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
    let loadedKey = null;
    let gen = 0;
    const viewKey = () => {
      if (ctx.view() === "live") return "live";
      const rs = ctx.status()?.replay;
      return rs && rs.name && rs.state !== "closed" && rs.state !== "loading" ? `replay|${rs.name}|${rs.t_end}` : null;
    };
    /** Clock history: the core's kept MissionState samples (live buffer, or the whole replay log), as the events list. */
    async function loadClock(key) {
      const my = ++gen;
      loadedKey = key;
      try {
        const res = await ctx.api.request("get_events", { view: ctx.view() }, 60000);
        if (my !== gen) return;
        const keep = ctx.view() === "live" ? feed.clock.samples.slice() : [];
        feed.reset();
        feed.feedAll((res.packets || []).filter((p) => p.type === M));
        const upto = feed.lastSample;
        for (const s of keep) if (s.t > upto) feed.clock.add(s.t, s.tm, s.phase, s.mode);
      } catch (e) {
        if (my === gen) loadedKey = null; // retry on the next tick
      }
    }
    const reset = () => {
      feed.reset();
      loadedKey = null;
      gen++;
      entries = [];
      lastPhase = undefined;
      renderLog();
    };
    if (compact) logSect.hidden = true;
    renderLog();
    return {
      onPacket(pkt) {
        if (pkt.type !== M) return;
        if (ctx.view() === "live") feed.feed(pkt); // replay: the clock comes from the whole log (loadReplay)
        if (pkt.f.mission_phase === undefined) return;
        if (lastPhase !== undefined && pkt.f.mission_phase !== lastPhase) {
          entries.push({ phase: `${lastPhase} → ${pkt.f.mission_phase}`, tm: feed.missionTime(pkt) === null ? "" : one(feed.missionTime(pkt)) });
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
      onTick() {
        if (ctx.api.state === "open") {
          const key = viewKey();
          if (key !== loadedKey) {
            if (key === null) loadedKey = null;
            else void loadClock(key);
          }
        }
        b.update();
      },
      freshness: () => b.freshness(),
    };
  },
});
