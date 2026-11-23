// Pure logic of the 'Simulation testing' page (docs/requests.md). SAFETY-RELEVANT UI rules, enforced here and in
// simtests_panel.js / simfaults_section.js:
//  - Every simulation test is greyed out and labelled 'simulation mode only' unless the VEHICLE reports simulation
//    mode (VehicleStatus.simulation === true), in the live view, on a control host, with a route. Display gating only:
//    the vehicle refuses everything outside simulation mode itself.
//  - A fault is ONE request, sent after a 1 s hold; its result is the vehicle's ACK in plain words.
//  - The GS computes nothing for the vehicle: the degree to radian conversion is the unit the vehicle's request wants.
import { describeRequest, latestOf } from "../requests/requests_logic.js";
import { windState } from "./simwind_logic.js";

export const FLAPS = ["TL", "TR", "BL", "BR"]; // flap index 0..3
export const FLAP_NAMES = { TL: "top left", TR: "top right", BL: "bottom left", BR: "bottom right" };
export const AT_CURRENT_ANGLE = -1.0; // SimFlapStuck special value: hold the current angle
export const FAULT_ACTIONS = ["SIM_FLAP_STUCK", "SIM_FLAP_SLOW", "SIM_FAULT_CLEAR"];
export const DEFAULT_STUCK_DEG = 42;
export const DEFAULT_SLOW_FACTOR = 0.3;
export const SIM_ONLY_LABEL = "simulation mode only";

export const degToRad = (deg) => (deg * Math.PI) / 180;
export const radToDeg = (rad) => (rad * 180) / Math.PI;

/** The `arg_i` of SIM_FLAP_STUCK / SIM_FLAP_SLOW: the flap index 0..3. */
export function flapIndex(flap) {
  const i = FLAPS.indexOf(flap);
  if (i < 0) throw new Error(`unknown flap ${flap}`);
  return i;
}

/** Strict number parse within [lo, hi]; null when empty, not finite or outside. */
export function parseRange(text, lo, hi) {
  const t = String(text).trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) && v >= lo && v <= hi ? v : null;
}

/**
 * The force-flap request for the form: {action, arg_i, arg_f} or null when invalid.
 * `atCurrent` (hold the current angle) sends -1.0; otherwise degrees 0..90 are converted to radians.
 */
export function stuckCommand({ flap, atCurrent, deg }) {
  let rad;
  if (atCurrent) rad = AT_CURRENT_ANGLE;
  else {
    const d = parseRange(deg, 0, 90);
    if (d === null) return null;
    rad = Math.min(degToRad(d), Math.PI / 2);
  }
  return { action: "SIM_FLAP_STUCK", arg_i: flapIndex(flap), arg_f: rad };
}

/** The slow-servo request: factor within the whitelist range (0.05..1), or null. */
export function slowCommand(cmds, { flap, factor }) {
  const r = (cmds || {}).SIM_FLAP_SLOW?.arg_f;
  const v = parseRange(factor, r ? r.min : 0.05, r ? r.max : 1);
  return v === null ? null : { action: "SIM_FLAP_SLOW", arg_i: flapIndex(flap), arg_f: v };
}

/** The clear request (no argument). */
export const clearCommand = () => ({ action: "SIM_FAULT_CLEAR" });

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

/** Plain-words state of the newest fault request (types 20..22), from the core's report only. */
export function describeFaultSent(reqs) {
  const rec = latestOf(reqs, FAULT_ACTIONS);
  if (!rec) return { tone: "dim", text: "no fault request sent since the core started" };
  const d = describeRequest(rec);
  return { tone: d.tone, text: `${rec.label || rec.action}: ${d.text}` };
}
