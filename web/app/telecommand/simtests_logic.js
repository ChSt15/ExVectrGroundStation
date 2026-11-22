// Pure logic of the 'Simulation testing' page (docs/telecommands.md, owner approval 2026-11-21). TEMPORARY like the
// whole telecommand path. SAFETY-RELEVANT UI rules, enforced here and in simtests_panel.js / simfaults_section.js:
//  - Every simulation test is greyed out and labelled 'simulation mode only' unless the VEHICLE reports simulation
//    mode (VehicleStatus.simulation === true), in the live view, on a control host, with a route. Display gating only:
//    the vehicle refuses everything outside simulation mode itself.
//  - A fault command is ONE frame, sent once after a 1 s hold, no retry, no ACK; the core limits it to one per 500 ms.
//  - The GS computes nothing for the vehicle: the degree to radian conversion is the unit the vehicle's packet wants.
import { windState } from "./simwind_logic.js";
import { describeSent, lastSent } from "./telecommand_logic.js";

export const FLAPS = ["TL", "TR", "BL", "BR"]; // flap index 0..3
export const FLAP_NAMES = { TL: "top left", TR: "top right", BL: "bottom left", BR: "bottom right" };
export const AT_CURRENT_ANGLE = -1.0; // SimFlapStuck special value: hold the current angle
export const TYPES = { SimFlapStuck: 16, SimFlapSlow: 17, SimFaultClear: 18 };
export const FAULT_TYPES = Object.keys(TYPES);
export const DEFAULT_STUCK_DEG = 42;
export const DEFAULT_SLOW_FACTOR = 0.3;
export const SIM_ONLY_LABEL = "simulation mode only";

export const degToRad = (deg) => (deg * Math.PI) / 180;
export const radToDeg = (rad) => (rad * 180) / Math.PI;

/** The `subsystem` byte of SimFlapStuck / SimFlapSlow: the flap index 0..3 only (no arm bit any more). */
export function stuckSubsystem(flap) {
  const i = FLAPS.indexOf(flap);
  if (i < 0) throw new Error(`unknown flap ${flap}`);
  return i;
}

/** The whitelist row with this telecommand type and subsystem byte, or null. */
export function findEntry(cmds, type, subsystem) {
  return (cmds || []).find((c) => c.type === type && c.subsystem === subsystem) || null;
}

/** Strict number parse within [lo, hi]; null when empty, not finite or outside. */
export function parseRange(text, lo, hi) {
  const t = String(text).trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) && v >= lo && v <= hi ? v : null;
}

/**
 * The force-flap command for the form: {name, value, rad, text} or null when invalid.
 * `atCurrent` (hold the current angle) sends -1.0; otherwise degrees 0..90 are converted to radians.
 */
export function stuckCommand(cmds, { flap, atCurrent, deg }) {
  const entry = findEntry(cmds, "SimFlapStuck", stuckSubsystem(flap));
  if (!entry) return null;
  let rad;
  if (atCurrent) rad = AT_CURRENT_ANGLE;
  else {
    const d = parseRange(deg, 0, 90);
    if (d === null) return null;
    rad = Math.min(degToRad(d), Math.PI / 2);
  }
  return { name: entry.name, value: rad };
}

/** The slow-servo command: factor within the whitelist range (0.05..1), or null. */
export function slowCommand(cmds, { flap, factor }) {
  const entry = findEntry(cmds, "SimFlapSlow", FLAPS.indexOf(flap));
  if (!entry) return null;
  const v = parseRange(factor, entry.param_float_min ?? 0.05, entry.param_float_max ?? 1);
  return v === null ? null : { name: entry.name, value: v };
}

/** The clear command (no value). */
export function clearCommand(cmds) {
  const entry = findEntry(cmds, "SimFaultClear", 0);
  return entry ? { name: entry.name, value: 0 } : null;
}

export const FORCED_DIFF_RAD = degToRad(2); // 'forced?' hint: commanded vs true angle differ by more than this ...
export const FORCED_HOLD_S = 1; // ... for more than this long

/**
 * Display-only 'forced?' hint per flap from FlapState {angle[4], cmd[4]} (rad), the vehicle's own report. `track` is a
 * mutable {since:[4]} the caller keeps between calls; `now` in seconds. Returns {forced:[bool x4], angle:[deg|null x4]}.
 * Never sent anywhere. Without FlapState (or without cmd) every flap is unknown (forced false, no hint).
 */
export function forcedFlaps(track, fs, now) {
  const out = { forced: [false, false, false, false], angle: [null, null, null, null] };
  if (!track.since) track.since = [null, null, null, null];
  for (let i = 0; i < 4; i++) {
    const a = fs && Array.isArray(fs.angle) ? fs.angle[i] : undefined;
    const c = fs && Array.isArray(fs.cmd) ? fs.cmd[i] : undefined;
    if (typeof a === "number" && Number.isFinite(a)) out.angle[i] = radToDeg(a);
    if (!(typeof a === "number" && Number.isFinite(a) && typeof c === "number" && Number.isFinite(c))) { track.since[i] = null; continue; }
    if (Math.abs(a - c) > FORCED_DIFF_RAD) {
      if (track.since[i] === null) track.since[i] = now;
      out.forced[i] = now - track.since[i] > FORCED_HOLD_S;
    } else track.since[i] = null;
  }
  return out;
}

/** Enabled/disabled for any simulation test: same gating as the wind buttons. */
export function simTestState(args) {
  return windState(args);
}

/** The newest fault telecommand (types 16..18) from the core's push, or null. */
export function lastFaultSent(push) {
  const recent = ((push && push.recent) || []).filter((r) => FAULT_TYPES.includes(r.type));
  return recent.length ? recent[0] : null;
}

/** 'sent N s ago via X, no ACK exists' for the newest fault command, from the core's record only. */
export function describeFaultSent(push, now) {
  const rec = lastFaultSent(push);
  if (!rec) return { tone: "dim", text: "no fault command sent since the core started" };
  const d = describeSent(rec, now);
  return { tone: d.tone, text: `${rec.label || rec.name}: ${d.text}` };
}

export { lastSent };
