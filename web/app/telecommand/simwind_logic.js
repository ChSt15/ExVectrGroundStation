// Pure logic of the 'Sim wind' panel (docs/telecommands.md, owner approval 2026-11-21). TEMPORARY like the whole
// telecommand path. SAFETY-RELEVANT UI rules, enforced here and in simwind_panel.js:
//  - Sending needs a deliberate hold of 1 s (one hold per group: Apply = 5 frames, Clear = 2 frames), never automatic.
//  - Every frame is sent exactly ONCE through `telecommand.send`: no retry, no repeat. The sequence stops at the first
//    refusal. The frames are spaced 150 ms (the vehicle handles one command per 100 ms tick).
//  - Enabled only while the VEHICLE reports simulation mode (display gating; the vehicle refuses otherwise anyway).
//  - Nothing is shown about the outcome except what the vehicle reports (simulation state).
import { HoldTimer, TELECOMMAND_HOLD_MS, describeSent, lastSent, reportedSimulation } from "./telecommand_logic.js";

export { HoldTimer, TELECOMMAND_HOLD_MS, describeSent, lastSent, reportedSimulation };
export const SEQUENCE_SPACING_MS = 150;
export const WIND_NAMES = ["SimWindSpeed", "SimWindDirection", "SimGustStrength", "SimGustPeriod", "SimGustSeed"];
export const DIRECTION_MODES = {
  from: "Wind from (compass, clockwise: 0 = N, 90 = E)",
  toward: "Air moves toward (counterclockwise from N: 90 = W)",
};

const TWO_PI = 2 * Math.PI;

/** Wrap an angle in rad to (-pi, pi]. */
export function wrapPi(x) {
  let w = x - TWO_PI * Math.round(x / TWO_PI);
  if (w <= -Math.PI) w += TWO_PI; // (-pi, pi]
  return Object.is(w, -0) ? 0 : w;
}

/**
 * Degrees in the chosen convention -> the vehicle's direction in rad: the direction the air moves TOWARD,
 * counterclockwise from North toward West (NWU), wrapped to +-pi.
 *  mode "from":   compass bearing the wind comes from, clockwise (0 = N, 90 = E). The air moves toward from+180
 *                 clockwise, which is -(from+180) counterclockwise.
 *  mode "toward": already counterclockwise from North (0 = toward N, 90 = toward W).
 */
export function degToVehicleRad(deg, mode) {
  const d = mode === "from" ? -(deg + 180) : deg;
  return wrapPi((d * Math.PI) / 180);
}

/** Inverse, for display of the converted value in degrees in the other convention. */
export function vehicleRadToDeg(rad, mode) {
  const ccw = (rad * 180) / Math.PI;
  if (mode === "toward") return ccw;
  const from = -ccw - 180;
  return ((from % 360) + 360) % 360;
}

/** Parse a number field within [lo, hi]; null when empty, not finite or outside. */
export function parseNumber(text, lo, hi) {
  const t = String(text).trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) && v >= lo && v <= hi ? v : null;
}

/** The five frames of Apply, in order: [{name, value}], or null when a field is invalid (see `fields`). */
export function applySequence({ speed, directionRad, gust, period, seed }) {
  const vals = [speed, directionRad, gust, period, seed];
  if (vals.some((v) => v === null || v === undefined || !Number.isFinite(v))) return null;
  return WIND_NAMES.map((name, i) => ({ name, value: vals[i] }));
}

/** The two frames of Clear wind: wind speed 0 and gust strength 0. */
export function clearSequence() {
  return [
    { name: "SimWindSpeed", value: 0 },
    { name: "SimGustStrength", value: 0 },
  ];
}

/**
 * Send the steps one after the other, `spacingMs` apart, exactly once each. `send(step)` returns a promise and throws
 * on refusal; the sequence STOPS at the first refusal (nothing is retried). `sleep(ms)` is injectable for tests.
 * Returns {sent: n, error: null | {step, message}}.
 */
export async function runSequence(steps, send, sleep, spacingMs = SEQUENCE_SPACING_MS) {
  let sent = 0;
  for (let i = 0; i < steps.length; i++) {
    if (i > 0) await sleep(spacingMs);
    try {
      await send(steps[i]);
    } catch (e) {
      return { sent, error: { step: steps[i], message: e && e.message ? e.message : String(e) } };
    }
    sent++;
  }
  return { sent, error: null };
}

/** Whether Apply/Clear can be used. `simulation` is VehicleStatus.simulation as the vehicle reports it. */
export function windState({ apiState, view, canControl, route, simulation, inputsOk = true, busy = false }) {
  if (view !== "live") return { enabled: false, reason: "replay view: nothing is ever sent" };
  if (apiState !== "open") return { enabled: false, reason: "no connection to the core" };
  if (!canControl) return { enabled: false, reason: "this client is on a view-only host" };
  if (!route) return { enabled: false, reason: "no connected source reaches the vehicle" };
  if (simulation !== true) return { enabled: false, reason: "the vehicle does not report simulation mode" };
  if (busy) return { enabled: false, reason: "sending" };
  if (!inputsOk) return { enabled: false, reason: "a value is outside its allowed range" };
  return { enabled: true, reason: "" };
}

/** Wind arrow in the 3D view: unit vector in NWU (x North, y West) the air moves toward, from the direction in rad. */
export function windVectorNwu(speed, directionRad) {
  return [speed * Math.cos(directionRad), speed * Math.sin(directionRad), 0];
}
