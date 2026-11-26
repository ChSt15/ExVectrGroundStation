// Signal catalogue of the tuning graphs (docs/gui.md "Tuning view"): every plottable value the vehicle actually
// sends, how it is read from a packet, its display unit, and the ready-made graphs. Pure, unit tested.
//
// Values are vehicle-reported. Derived values (roll/pitch/yaw, target minus current, calibrated sensors computed with
// the ship's formulas from CALIBRATION) are display-only arithmetic on received packets. Angles are shown in degrees
// and rates in deg/s (the packets are SI: rad, rad/s); the unit is always on the axis and in the legend.
import { quatToRpy, tiltFromVertical } from "../fmt.js";
import { plotGnssAltitude, plotGnssFromHome } from "../gnss/gnss_logic.js";
import { computeCalibrated } from "../sensors/rawsensors_logic.js";

const D = 180 / Math.PI;
const num = (v) => (typeof v === "number" ? v : v === null ? NaN : undefined);
const at = (arr, i, scale = 1) => (Array.isArray(arr) ? (typeof arr[i] === "number" ? arr[i] * scale : arr[i] === null ? NaN : undefined) : undefined);
const AX = ["x", "y", "z"];
const NWU = ["N", "W", "U"];
const FLAPS = ["TL", "TR", "BL", "BR"];

function sig(id, label, group, unit, type, extract, desc = "") {
  return { id, label, group, unit, type, extract, desc };
}

/** Build the catalogue. extract(f, last) gets the packet's fields and the latest fields per type seen before it. */
function build() {
  const s = [];
  // Attitude estimate
  ["roll", "pitch", "yaw"].forEach((n, i) =>
    s.push(sig(`att.${n}`, `${n[0].toUpperCase()}${n.slice(1)}`, "Attitude", "deg", "Attitude", (f) => {
      const r = quatToRpy(f.q);
      return r ? r[i] * D : f.q === null ? NaN : undefined;
    }, "ZYX angles from the attitude quaternion (display only)")),
  );
  s.push(sig("att.tilt", "Tilt from vertical", "Attitude", "deg", "Attitude", (f) => {
    const t = tiltFromVertical(f.q);
    return t === undefined ? undefined : t * D;
  }));
  AX.forEach((a, i) => s.push(sig(`rate.${a}`, `Rate ${a} (est.)`, "Angular rate", "deg/s", "Attitude", (f) => at(f.omega, i, D), "Attitude.omega, body frame")));
  // Position / velocity vs target
  NWU.forEach((a, i) => {
    s.push(sig(`pos.${a}`, `Position ${a}`, "Position", "m", "Position", (f) => at(f.pos, i)));
    s.push(sig(`sp.pos.${a}`, `Target position ${a}`, "Position", "m", "MissionState", (f) => at(f.sp_pos, i)));
    s.push(sig(`vel.${a}`, `Velocity ${a}`, "Velocity", "m/s", "Position", (f) => at(f.vel, i)));
    s.push(sig(`sp.vel.${a}`, `Target velocity ${a}`, "Velocity", "m/s", "MissionState", (f) => at(f.sp_vel, i)));
    s.push(sig(`err.pos.${a}`, `Position error ${a}`, "Guidance error", "m", "MissionState", (f, last) => {
      const t = at(f.sp_pos, i);
      const p = last.Position ? at(last.Position.pos, i) : undefined;
      return t === undefined || p === undefined ? undefined : t - p;
    }, "target minus the latest position at the MissionState time (display only)"));
    s.push(sig(`err.vel.${a}`, `Velocity error ${a}`, "Guidance error", "m/s", "MissionState", (f, last) => {
      const t = at(f.sp_vel, i);
      const v = last.Position ? at(last.Position.vel, i) : undefined;
      return t === undefined || v === undefined ? undefined : t - v;
    }, "target minus the latest velocity (display only)"));
  });
  s.push(sig("vel.h", "Horizontal speed", "Velocity", "m/s", "Position", (f) => (Array.isArray(f.vel) && typeof f.vel[0] === "number" && typeof f.vel[1] === "number" ? Math.hypot(f.vel[0], f.vel[1]) : undefined)));
  // Actuators (commanded outputs of the vehicle)
  FLAPS.forEach((n, i) => s.push(sig(`act.flap.${n}`, `Flap ${n} (Actuators)`, "Actuators", "deg", "Actuators", (f) => at(f.flaps, i, D))));
  ["x", "y"].forEach((a, i) => s.push(sig(`act.tvc.${a}`, `TVC ${a}`, "Actuators", "deg", "Actuators", (f) => at(f.tvc, i, D))));
  s.push(sig("act.ground_distance", "Ground distance", "Actuators", "m", "Actuators", (f) => num(f.ground_distance), "downward lidar; NaN gaps when it has no valid reading (out of range, stale)"));
  s.push(sig("act.thrust", "Thrust", "Actuators", "N", "Actuators", (f) => num(f.thrust)));
  s.push(sig("act.roll_torque", "Roll torque (air set only)", "Actuators", "N*m", "Actuators", (f) => num(f.roll_torque)));
  FLAPS.forEach((n, i) => {
    s.push(sig(`flap.${n}`, `Flap ${n} angle (FlapState)`, "Flaps", "deg", "FlapState", (f) => at(f.angle, i, D)));
    s.push(sig(`flap.cmd.${n}`, `Flap ${n} command (FlapState)`, "Flaps", "deg", "FlapState", (f) => at(f.cmd, i, D)));
  });
  ["xp", "xn", "yp", "yn"].forEach((n, i) => s.push(sig(`tvc.vane.${n}`, `Vane ${n}`, "TVC state", "deg", "TvcState", (f) => at(f.vane, i, D))));
  ["m1", "m2"].forEach((n, i) => s.push(sig(`tvc.motor.${n}`, `Motor ${n}`, "TVC state", "1", "TvcState", (f) => at(f.motor, i))));
  AX.forEach((a, i) => s.push(sig(`vs.thrust.${a}`, `TVC thrust ${a} (VehicleState)`, "TVC state", "N", "VehicleState", (f) => at(f.tvc_thrust, i))));
  // IMU: raw, calibrated (computed with the ship's formulas), estimated
  const cal = (f, last) => (last.Calibration ? computeCalibrated(f, last.Calibration) : null);
  AX.forEach((a, i) => {
    s.push(sig(`imu.gyro.${a}`, `Gyro ${a} raw`, "Gyro", "deg/s", "ImuRaw", (f) => at(f.gyro, i, D)));
    s.push(sig(`cal.gyro.${a}`, `Gyro ${a} calibrated`, "Gyro", "deg/s", "ImuRaw", (f, last) => {
      const c = cal(f, last);
      return c && c.gyro ? c.gyro[i] * D : undefined;
    }, "M (raw - offset) from CALIBRATION, computed here (display only)"));
    s.push(sig(`est.gyro.${a}`, `Gyro ${a} calibrated - bias est.`, "Gyro", "deg/s", "ImuRaw", (f, last) => {
      const c = cal(f, last);
      return c && c.omega_est ? c.omega_est[i] * D : undefined;
    }, "omega_est = gyro_cal - gyro_bias_est (display only)"));
    s.push(sig(`imu.accel.${a}`, `Accel ${a} raw`, "Accelerometer", "m/s²", "ImuRaw", (f) => at(f.accel, i)));
    s.push(sig(`cal.accel.${a}`, `Accel ${a} calibrated`, "Accelerometer", "m/s²", "ImuRaw", (f, last) => {
      const c = cal(f, last);
      return c && c.accel ? c.accel[i] : undefined;
    }));
    s.push(sig(`imu.mag.${a}`, `Mag ${a} raw`, "Magnetometer", "µT", "ImuRaw", (f) => at(f.mag, i, 1e6)));
    s.push(sig(`cal.mag.${a}`, `Mag ${a} calibrated`, "Magnetometer", "µT", "ImuRaw", (f, last) => {
      const c = cal(f, last);
      return c && c.mag ? c.mag[i] * 1e6 : undefined;
    }));
  });
  s.push(sig("imu.accel.norm", "|Accel| raw", "Accelerometer", "m/s²", "ImuRaw", (f) => (Array.isArray(f.accel) && f.accel.every((x) => typeof x === "number") ? Math.hypot(...f.accel) : undefined)));
  s.push(sig("imu.pressure", "Pressure", "Barometer", "Pa", "ImuRaw", (f) => num(f.pressure)));
  // Estimator uncertainty
  NWU.forEach((a, i) => s.push(sig(`std.att.${a}`, `Attitude σ about ${a}`, "Estimator σ", "deg", "Estimator", (f) => at(f.att_std, i, D), "1 sigma of rotation about the world axes")));
  AX.forEach((a, i) => {
    s.push(sig(`std.vel.${a}`, `Velocity σ ${a}`, "Estimator σ", "m/s", "Estimator", (f) => at(f.vel_std, i)));
    s.push(sig(`std.pos.${a}`, `Position σ ${a}`, "Estimator σ", "m", "Estimator", (f) => at(f.pos_std, i)));
  });
  // GNSS altitude vs the vehicle's estimate (display only, gnss/gnss_logic.js)
  s.push(sig("gnss.alt", "GNSS altitude (absolute)", "GNSS altitude", "m", "Gps", (f) => (f.alt === null ? NaN : num(f.alt)), "receiver altitude, u-blox: above the WGS-84 ellipsoid"));
  s.push(sig("gnss.alt_rel", "GNSS altitude (relative to start)", "GNSS altitude", "m", "Gps", (f, last) => plotGnssAltitude(f, last).rel, "minus the HOME_POSITION altitude, else the mean of the first GNSS altitudes on the pad"));
  s.push(sig("gnss.alt_diff", "Altitude difference (estimated - GNSS)", "GNSS altitude", "m", "Gps", (f, last) => {
    const r = plotGnssAltitude(f, last);
    return r.rel === undefined || r.est === undefined ? undefined : r.est - r.rel;
  }, "Position up minus the relative GNSS altitude"));
  // GNSS position in metres relative to the vehicle's home, NWU (derived: latest HomePosition + Gps lat/lon/alt)
  NWU.forEach((a, i) => s.push(sig(`gnss.home.${a}`, `GNSS position ${a} (relative to home)`, "GNSS position", "m", "Gps", (f, last) => plotGnssFromHome(f, last, i), "derived in the ground station from the latest HOME_POSITION and the GNSS lat/lon/alt (WGS-84 tangent plane); needs a home position")));
  s.push(sig("gnss.home.h", "GNSS horizontal distance from home", "GNSS position", "m", "Gps", (f, last) => {
    const n = plotGnssFromHome(f, last, 0);
    return n === undefined ? undefined : Math.hypot(n, plotGnssFromHome(f, last, 1));
  }, "derived, see GNSS position N"));
  // Control-loop timing (task names are not in the telemetry)
  for (let i = 0; i < 8; i++) {
    s.push(sig(`timing.avg.${i}`, `Task ${i} avg`, "Task timing", "ms", "TaskTiming", (f) => at(f.avg, i, 1e3)));
    s.push(sig(`timing.max.${i}`, `Task ${i} max`, "Task timing", "ms", "TaskTiming", (f) => at(f.max, i, 1e3)));
  }
  return s;
}

export const SIGNALS = build();
export const SIGNAL_BY_ID = new Map(SIGNALS.map((x) => [x.id, x]));
/** Packet types needed by the catalogue (subscription and history). Calibration is only an input. */
export const SIGNAL_TYPES = [...new Set([...SIGNALS.map((x) => x.type), "Calibration", "HomePosition", "Position"])].sort();
export const MAX_SERIES = 8; // per graph: the categorical palette has eight slots

/**
 * Ready-made graphs. `series`: [signal id, colour slot, dashed]. A target and its current value share the colour
 * slot (same entity), the target is dashed. One unit per graph (no second y axis).
 */
export const GRAPH_PRESETS = [
  { id: "attitude", title: "Attitude roll / pitch / yaw", series: [["att.roll", 0], ["att.pitch", 1], ["att.yaw", 2]], note: "attitude target: not in the telemetry" },
  { id: "rates", title: "Angular rate (estimated)", series: [["rate.x", 0], ["rate.y", 1], ["rate.z", 2]], note: "rate target: not in the telemetry" },
  { id: "pos_up", title: "Altitude vs target", series: [["pos.U", 2], ["sp.pos.U", 2, true]] },
  { id: "alt_check", title: "Altitude cross-check: estimated vs GNSS", series: [["pos.U", 2], ["gnss.alt_rel", 1], ["gnss.alt_diff", 3]], note: "GNSS relative to the home altitude or the start" },
  { id: "gnss_home", title: "GNSS position relative to home vs estimate", series: [["gnss.home.N", 0], ["pos.N", 0, true], ["gnss.home.W", 1], ["pos.W", 1, true]], note: "GNSS metres derived from HOME_POSITION; needs a home position" },
  { id: "pos_nw", title: "Position N / W vs target", series: [["pos.N", 0], ["sp.pos.N", 0, true], ["pos.W", 1], ["sp.pos.W", 1, true]] },
  { id: "vel", title: "Velocity vs target", series: [["vel.N", 0], ["sp.vel.N", 0, true], ["vel.W", 1], ["sp.vel.W", 1, true], ["vel.U", 2], ["sp.vel.U", 2, true]] },
  { id: "pos_err", title: "Position error (target − current)", series: [["err.pos.N", 0], ["err.pos.W", 1], ["err.pos.U", 2]] },
  { id: "tvc", title: "TVC deflection", series: [["act.tvc.x", 0], ["act.tvc.y", 1]] },
  { id: "thrust", title: "Thrust", series: [["act.thrust", 3]] },
  { id: "flaps", title: "Flaps: command (Actuators) vs angle (FlapState)", series: [["act.flap.TL", 0, true], ["flap.TL", 0], ["act.flap.TR", 1, true], ["flap.TR", 1], ["act.flap.BL", 2, true], ["flap.BL", 2], ["act.flap.BR", 3, true], ["flap.BR", 3]] },
  { id: "gyro_x", title: "Gyro x: raw / calibrated / estimated", series: [["imu.gyro.x", 0], ["cal.gyro.x", 1], ["est.gyro.x", 2], ["rate.x", 3]] },
  { id: "accel", title: "Accelerometer raw vs calibrated", series: [["imu.accel.x", 0], ["cal.accel.x", 0, true], ["imu.accel.y", 1], ["cal.accel.y", 1, true], ["imu.accel.z", 2], ["cal.accel.z", 2, true]] },
  { id: "mag", title: "Magnetometer raw vs calibrated", series: [["imu.mag.x", 0], ["cal.mag.x", 0, true], ["imu.mag.y", 1], ["cal.mag.y", 1, true], ["imu.mag.z", 2], ["cal.mag.z", 2, true]] },
  { id: "std_att", title: "Estimator σ attitude (world axes)", series: [["std.att.N", 0], ["std.att.W", 1], ["std.att.U", 2]] },
  { id: "std_vel", title: "Estimator σ velocity", series: [["std.vel.x", 0], ["std.vel.y", 1], ["std.vel.z", 2]] },
  { id: "std_pos", title: "Estimator σ position", series: [["std.pos.x", 0], ["std.pos.y", 1], ["std.pos.z", 2]] },
  { id: "timing", title: "Control-loop task timing (avg)", series: [0, 1, 2, 3].map((i) => [`timing.avg.${i}`, i]) },
];
export const DEFAULT_GRAPHS = ["attitude", "rates", "pos_up", "vel", "tvc", "flaps", "gyro_x", "std_att"];

/** A graph definition from a preset id (deep copy), or null. */
export function presetGraph(id) {
  const p = GRAPH_PRESETS.find((g) => g.id === id);
  return p ? { preset: p.id, title: p.title, note: p.note || "", series: p.series.map(([sigId, slot, dash]) => ({ sig: sigId, slot, dash: !!dash })) } : null;
}

/** Validate a stored graph list (localStorage): unknown signals are dropped, at most MAX_SERIES per graph. */
export function sanitizeGraphs(raw) {
  if (!Array.isArray(raw)) return null;
  const out = [];
  for (const g of raw) {
    if (!g || typeof g !== "object" || !Array.isArray(g.series)) continue;
    const series = g.series
      .filter((x) => x && SIGNAL_BY_ID.has(x.sig))
      .slice(0, MAX_SERIES)
      .map((x) => ({ sig: x.sig, slot: Number.isInteger(x.slot) ? ((x.slot % MAX_SERIES) + MAX_SERIES) % MAX_SERIES : 0, dash: !!x.dash }));
    out.push({ preset: typeof g.preset === "string" ? g.preset : "", title: typeof g.title === "string" ? g.title.slice(0, 80) : "Graph", note: typeof g.note === "string" ? g.note : "", series });
  }
  return out;
}

/** Add or remove a signal; a new one takes the first colour slot not used in the graph. */
export function toggleSignal(graph, sigId) {
  const has = graph.series.findIndex((x) => x.sig === sigId);
  if (has >= 0) return { ...graph, series: graph.series.filter((_, i) => i !== has) };
  if (graph.series.length >= MAX_SERIES || !SIGNAL_BY_ID.has(sigId)) return graph;
  const used = new Set(graph.series.map((x) => x.slot));
  let slot = 0;
  while (used.has(slot) && slot < MAX_SERIES - 1) slot++;
  return { ...graph, series: [...graph.series, { sig: sigId, slot, dash: false }] };
}

/** Units of a graph's signals, for the axis label ("deg", or "m, m/s" when the user mixed them). */
export function graphUnits(graph) {
  return [...new Set(graph.series.map((x) => SIGNAL_BY_ID.get(x.sig)?.unit).filter(Boolean))];
}

/**
 * Feeds packets (in time order) into one RingSeries per signal. `last` keeps the latest fields per type, used by
 * the derived signals (target minus current, calibrated values). `make()` creates a ring buffer.
 */
export class SignalFeeder {
  constructor(make) {
    this.make = make;
    this.rings = new Map(); // signal id -> RingSeries
    this.last = {};
    this.byType = new Map();
    for (const x of SIGNALS) {
      if (!this.byType.has(x.type)) this.byType.set(x.type, []);
      this.byType.get(x.type).push(x);
    }
    this.supplied = new Set(); // signal ids that produced at least one value
  }

  ring(id) {
    let r = this.rings.get(id);
    if (!r) this.rings.set(id, (r = this.make()));
    return r;
  }

  clear() {
    for (const r of this.rings.values()) r.clear();
    this.last = {};
    this.supplied.clear();
  }

  /** One packet {type, t (s), f}. */
  push(type, t, f) {
    const sigs = this.byType.get(type);
    if (sigs) {
      for (const x of sigs) {
        let v;
        try {
          v = x.extract(f, this.last);
        } catch (e) {
          v = undefined;
        }
        if (v === undefined) continue;
        this.ring(x.id).push(t, v);
        this.supplied.add(x.id);
      }
    }
    this.last[type] = f;
  }
}

/**
 * History reply (get_history series with fields) to packets in time order: [{type, t, f}]. `nf` markers are not
 * part of history replies; nulls there are gaps.
 */
export function historyToPackets(series) {
  const out = [];
  for (const s of series || []) {
    const fields = Object.keys(s).filter((k) => !["type", "src", "t"].includes(k));
    for (let i = 0; i < (s.t || []).length; i++) {
      const f = {};
      for (const k of fields) if (s[k][i] !== undefined) f[k] = s[k][i];
      out.push({ type: s.type, t: s.t[i], f });
    }
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

/** Fields to request per type for the history load. */
export const HISTORY_FIELDS = {
  Attitude: ["q", "omega"],
  Position: ["pos", "vel"],
  Gps: ["alt", "fix_type", "lat", "lon"],
  MissionState: ["sp_pos", "sp_vel"],
  Actuators: ["flaps", "tvc", "thrust", "roll_torque"],
  FlapState: ["angle", "cmd"],
  TvcState: ["vane", "motor"],
  VehicleState: ["tvc_thrust"],
  ImuRaw: ["gyro", "accel", "mag", "pressure"],
  Estimator: ["att_std", "vel_std", "pos_std"],
  TaskTiming: ["avg", "max"],
};
