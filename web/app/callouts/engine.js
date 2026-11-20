// Callout rule engine (docs/callouts.md). Evaluates the vehicle-reported values in the client store and returns
// events { id, category, text, severity, priority, ... }. Pure and display-only: it decides nothing for the vehicle,
// it only says what the vehicle reported. Data older than `stale_s` never triggers mission, altitude or deviation
// callouts. Category enable switches are applied by the caller, not here (baselines keep following the data).

/** Spoken names of the failure_bits of VEHICLE_STATUS, same order as FAILURE_BITS (a test checks the length). */
export const FAILURE_TEXT = [
  "Sensor failure", "Attitude failure", "Position failure", "Position out of bounds", "Attitude out of bounds", "Radio connection loss",
];
export const failureText = (i) => FAILURE_TEXT[i] || `Failure bit ${i}`;

/** priority: lower number is more important (failure 0 ... countdown 5). */
export const PRIORITY = { failure: 0, warning: 1, state: 2, mission: 3, altitude: 4, countdown: 5 };

const MOTORS_REPEAT_S = 3;
const finite = (x) => typeof x === "number" && Number.isFinite(x);
const vec3 = (v) => Array.isArray(v) && v.length >= 3 && v.slice(0, 3).every(finite);

function ev(id, category, text, severity, priority, extra = {}) {
  return { id, category, text, severity, priority, ...extra };
}

export class CalloutEngine {
  /** @param {() => object} getSettings complete settings (settings.js), read on every evaluation */
  constructor(getSettings) {
    this.cfg = getSettings;
    this.reset();
  }

  /** Forget everything (live view start). The next pass announces failures and sim/real, nothing else. */
  reset() {
    this.s = { prevBits: 0, failsafe: false, fail: new Map(), lastNow: null, lost: false, routerUp: undefined };
    this.quiet = false;
  }

  /** Re-baseline silently (replay seek, view change): the next data pass fires nothing. */
  resync() {
    const keep = { lost: false };
    this.s = { prevBits: 0, failsafe: false, fail: new Map(), lastNow: null, ...keep, routerUp: undefined };
    this.quiet = true;
  }

  /**
   * One pass. `in`: { view: "live"|"replay", now (view seconds), status, mission, position: store entries or
   * undefined, sources: core source list (live only) }.
   * Returns { events, clears: [sticky keys that ended], resynced }.
   */
  evaluate(inp) {
    const c = this.cfg();
    const s = this.s;
    const now = inp.now;
    const out = [];
    const clears = [];
    let resynced = false;
    if (!finite(now)) return { events: out, clears, resynced };

    // a jump of the replay time (a seek) re-baselines silently; the live clock never jumps
    if (inp.view === "replay" && finite(s.lastNow) && (now < s.lastNow - 0.01 || now - s.lastNow > 2.0)) {
      this.resync();
      resynced = true;
    }
    const st = this.s;
    st.lastNow = now;

    const fresh = (e) => !!e && !!e.f && now - e.t <= c.stale_s;
    const status = fresh(inp.status) ? inp.status : null;
    const mission = fresh(inp.mission) ? inp.mission : null;
    const position = fresh(inp.position) ? inp.position : null;
    const live = inp.view === "live";
    const mode = (mission && mission.f.mission_mode) ?? (status && status.f.mission_mode);
    const phase = (mission && mission.f.mission_phase) ?? (status && status.f.mission_phase);

    // ---- link / telemetry (live only)
    if (live) {
      if (inp.status) {
        const lost = now - inp.status.t > c.telemetry.timeout_s;
        if (lost && !st.lost) out.push(ev("link.telemetry_lost", "link", "Telemetry lost", "warning", PRIORITY.warning, { key: "telemetry" }));
        if (!lost && st.lost) {
          out.push(ev("link.telemetry_restored", "link", "Telemetry restored", "ok", PRIORITY.state));
          clears.push("telemetry");
        }
        st.lost = lost;
      }
      const r = (inp.sources || []).find((x) => x.kind === "link_module");
      if (r && r.state !== "disabled") {
        const up = r.state === "connected";
        if (st.routerUp === true && !up) out.push(ev("link.router_lost", "link", "Router link lost", "warning", PRIORITY.warning, { key: "router" }));
        if (st.routerUp === false && up) {
          out.push(ev("link.router_restored", "link", "Router link restored", "ok", PRIORITY.state));
          clears.push("router");
        }
        st.routerUp = up;
      }
    } else {
      st.lost = false;
    }

    // ---- VehicleStatus: failures, state, sim/real
    if (status) {
      const f = status.f;
      this._failures(c, f, now, out, clears);
      this._state(f, now, out);
    }

    // ---- mission and altitude (stale data resets the baselines so that the first fresh pass is silent)
    this._countdown(c, mission, out);
    this._phase(c, phase, mission || status, out);
    const airborne = mode === "Running" && (mission || status);
    this._altitude(c, airborne ? position : null, out);
    this._deviation(c, airborne ? position : null, mission, now, out);

    if (this.quiet && (status || mission || position)) {
      this.quiet = false;
      out.length = 0;
      clears.length = 0;
    } else if (this.quiet) {
      out.length = 0;
    }
    return { events: out, clears, resynced };
  }

  _failures(c, f, now, out, clears) {
    const st = this.s;
    const active = new Map();
    if (finite(f.failure_bits)) {
      for (let i = 0; i < 32; i++) if (f.failure_bits & (1 << i)) active.set(`bit${i}`, { text: failureText(i), id: `failure.bit.${i}` });
    }
    if (f.failsafe === true) active.set("failsafe", { text: "Failsafe", id: "failure.failsafe" });
    let cleared = false;
    for (const [k, v] of active) {
      let tr = st.fail.get(k);
      if (!tr) {
        tr = { count: 0, next: now + c.failure.repeat_s };
        st.fail.set(k, tr);
        out.push(ev(v.id, "failure", v.text, "failure", PRIORITY.failure, { key: `failure:${k}`, sticky: true }));
      } else if (tr.count < c.failure.repeats && now >= tr.next) {
        tr.count += 1;
        tr.next = now + c.failure.repeat_s;
        out.push(ev(v.id, "failure", v.text, "failure", PRIORITY.failure, { key: `failure:${k}`, sticky: true, repeat: true }));
      }
    }
    for (const k of [...st.fail.keys()]) {
      if (!active.has(k)) {
        st.fail.delete(k);
        clears.push(`failure:${k}`);
        cleared = true;
      }
    }
    if (cleared) out.push(ev("failure.cleared", "failure", "Failure cleared", "ok", PRIORITY.state));
    st.failsafe = f.failsafe === true;
  }

  _state(f, now, out) {
    const st = this.s;
    const edge = (key, v, rise, fall) => {
      if (typeof v !== "boolean") return;
      const prev = st[key];
      st[key] = v;
      if (prev === undefined || prev === v) return;
      const e = v ? rise : fall;
      if (e) out.push(e);
    };
    edge("armed", f.armed, ev("state.armed", "state", "Armed", "warning", PRIORITY.state), ev("state.disarmed", "state", "Disarmed", "info", PRIORITY.state));
    edge("motors", f.motors_enabled, ev("state.motors", "state", "Motors enabled", "failure", PRIORITY.failure), null);
    // "Motors enabled" is said twice (once more after MOTORS_REPEAT_S) while the vehicle still reports it
    if (st.motors === true && st.motorsAt === undefined && out.some((e) => e.id === "state.motors")) st.motorsAt = now + MOTORS_REPEAT_S;
    if (st.motors !== true) st.motorsAt = undefined;
    else if (typeof st.motorsAt === "number" && now >= st.motorsAt) {
      st.motorsAt = null;
      out.push(ev("state.motors", "state", "Motors enabled", "failure", PRIORITY.failure, { repeat: true }));
    }
    edge("zeroing", f.zeroing, ev("state.zeroing", "state", "Zeroing", "info", PRIORITY.state), ev("state.zero_done", "state", "Zero complete", "ok", PRIORITY.state));
    if (typeof f.simulation === "boolean" && st.sim !== f.simulation) {
      const first = st.sim === undefined;
      st.sim = f.simulation;
      out.push(ev("state.sim", "state", f.simulation ? "Simulation" : "Real sensors", first ? "info" : "warning", PRIORITY.state));
    }
  }

  _countdown(c, mission, out) {
    const st = this.s;
    const tm = mission && finite(mission.f.t_mission) ? mission.f.t_mission : null;
    if (tm === null) {
      st.tm = undefined;
      return;
    }
    const prev = st.tm;
    st.tm = tm;
    if (prev === undefined) return;
    if (tm < prev || tm - prev > 2.0) return; // mission time reset or a jump: baseline only
    if (prev < 0 && tm >= 0) {
      out.push(ev("countdown.start", "countdown", "Mission start", "info", PRIORITY.countdown, { maxAge: 3 }));
      return;
    }
    let last = null; // only the last crossed number is spoken (older ones would be late)
    for (let n = 5; n >= 1; n--) if (prev < -n && tm >= -n) last = n;
    if (last !== null) out.push(ev(`countdown.${last}`, "countdown", String(last), "info", PRIORITY.countdown, { maxAge: 1.5 }));
  }

  _phase(c, phase, src, out) {
    const st = this.s;
    if (typeof phase !== "string" || !src) {
      st.phase = undefined;
      return;
    }
    const prev = st.phase;
    st.phase = phase;
    if (prev === undefined || prev === phase) return;
    const text = c.phaseNames[phase];
    if (text) out.push(ev(`phase.${phase}`, "phase", text, "info", PRIORITY.mission));
  }

  _altitude(c, position, out) {
    const st = this.s;
    const z = position && vec3(position.f.pos) ? position.f.pos[2] : null;
    if (z === null) {
      st.above = undefined;
      return;
    }
    const half = c.altitude.hysteresis_m / 2;
    const levels = c.altitude.levels;
    if (!st.above) {
      st.above = new Map(levels.map((l) => [l, z >= l]));
      return;
    }
    const up = [];
    const down = [];
    for (const l of levels) {
      const was = st.above.get(l);
      if (was === undefined) {
        st.above.set(l, z >= l);
      } else if (!was && z >= l + half) {
        st.above.set(l, true);
        up.push(l);
      } else if (was && z <= l - half) {
        st.above.set(l, false);
        down.push(l);
      }
    }
    for (const l of [...st.above.keys()]) if (!levels.includes(l)) st.above.delete(l);
    // several levels in one step: say only the one reached last
    if (up.length) out.push(ev(`altitude.up.${Math.max(...up)}`, "altitude", `Altitude ${Math.max(...up)}`, "info", PRIORITY.altitude, { maxAge: 4 }));
    if (down.length) out.push(ev(`altitude.down.${Math.min(...down)}`, "altitude", `Altitude ${Math.min(...down)}`, "info", PRIORITY.altitude, { maxAge: 4 }));
  }

  /** Position error against the MissionState setpoint. Shipped disabled (docs/callouts.md). */
  _deviation(c, position, mission, now, out) {
    const st = this.s;
    const p = position && vec3(position.f.pos) ? position.f.pos : null;
    const sp = mission && vec3(mission.f.sp_pos) ? mission.f.sp_pos : null;
    if (!p || !sp) {
      st.dev = undefined;
      return;
    }
    const err = Math.hypot(p[0] - sp[0], p[1] - sp[1], p[2] - sp[2]);
    const d = st.dev || (st.dev = { over: null, fired: false });
    if (err > c.deviation.limit_m) {
      if (d.over === null) d.over = now;
      if (!d.fired && now - d.over >= c.deviation.hold_s) {
        d.fired = true;
        out.push(ev("deviation.position", "deviation", "Position deviation", "warning", PRIORITY.warning, { key: "deviation" }));
      }
    } else if (err < c.deviation.limit_m - c.deviation.hysteresis_m) {
      d.over = null;
      d.fired = false;
    }
  }
}
