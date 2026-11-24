// Pure logic of the 'Mission parameters' rows of the Vehicle panel (docs/requests.md). Display and command only:
// nothing sends a parameter automatically. Ten rows are the whitelisted REQUEST SET_MISSION_PARAM (action 24, target 1,
// arg_i = parameter id, arg_f = value); the stop altitude is the eleventh, in the Flight group (SET_STOP_ALTITUDE, logic in stopalt_logic.js).
// The vehicle accepts them only on the ground with the session and answers with a REQUEST_ACK; the vehicle's values
// come back in the plain MISSION_PARAMS packet (the stop altitude in MissionState.stop_altitude).
import { refusalText } from "../requests/requests_logic.js";
import { describeSetting } from "./stopalt_logic.js";

export const ACTION = "SET_MISSION_PARAM";
export const TARGET = 1;
export const GAP_MS = 550; // between two requests (the core spaces requests by 500 ms)
export const TIMEOUT_MS = 7000; // per request, the client's backstop on top of the core's own retries
export const APOGEE_WARN_M = 90;
export const LATERAL_WARN_M = 0.5;
const FLIGHT = "Flight (profile 2)";
export const NOTE = "accepted only on the ground, motors disarmed; a vehicle reboot returns the defaults";
export const LATERAL_WARNING = "apogee not above home: the descent flies a lateral transfer to home";
export const APOGEE_WARNING = "below 90 m: no bellyflop, the flight ends in RTH";

/** Rows in display order. `field` = MissionParams field (null: MissionState.stop_altitude), `id` = arg_i. */
export const ROWS = [
  { key: "ascent_speed", id: 1, field: "ascent_speed", label: "Ascent speed", unit: "m/s", min: 0.5, max: 10, def: 7, group: FLIGHT, step: 0.5 },
  { key: "ascent_accel", id: 7, field: "ascent_accel", label: "Ascent acceleration", unit: "m/s^2", min: 0.2, max: 20, def: 3, group: FLIGHT, step: 0.1 },
  { key: "ascent_decel", id: 8, field: "ascent_decel", label: "Ascent deceleration", unit: "m/s^2", min: 0.2, max: 20, def: 2, group: FLIGHT, step: 0.1 },
  { key: "apogee_x", id: 9, field: "apogee_x", label: "Apogee position X (north)", fullLabel: "Apogee position X (north) m", unit: "m", min: -2000, max: 2000, def: 0, group: FLIGHT, step: 0.5 },
  { key: "apogee_y", id: 10, field: "apogee_y", label: "Apogee position Y (west)", fullLabel: "Apogee position Y (west) m", unit: "m", min: -2000, max: 2000, def: 0, group: FLIGHT, step: 0.5 },
  { key: "apogee_altitude", id: 2, field: "apogee_altitude", label: "Apogee altitude Z (up)", fullLabel: "Apogee altitude Z (up) m", unit: "m", min: 2, max: 1000, def: 120, group: FLIGHT, step: 1 },
  { key: "stop_altitude", id: null, field: null, label: "Stop altitude", unit: "m", min: 1, max: Infinity, def: 20, group: FLIGHT, step: 0.5, action: "SET_STOP_ALTITUDE" },
  { key: "hover_hold_s", id: 3, field: "hover_hold_s", label: "Hover hold", unit: "s", min: 0, max: 600, def: 30, group: "Hover test (profile 0)", step: 1 },
  { key: "p1_hold_1_s", id: 4, field: "p1_hold_1_s", label: "Hold 1 (at 1 m)", unit: "s", min: 0, max: 600, def: 10, group: "Waypoint test (profile 1)", step: 1 },
  { key: "p1_hold_2_s", id: 5, field: "p1_hold_2_s", label: "Hold 2 (at 2 m x)", unit: "s", min: 0, max: 600, def: 15, group: "Waypoint test (profile 1)", step: 1 },
  { key: "p1_hold_3_s", id: 6, field: "p1_hold_3_s", label: "Hold 3 (at 2 m x and 2 m y)", unit: "s", min: 0, max: 600, def: 15, group: "Waypoint test (profile 1)", step: 1 },
];
export const rowAction = (row) => row.action || ACTION;
const TOL = 0.0005; // f32 readback

const finite = (x) => typeof x === "number" && Number.isFinite(x);

/** The field text as a number within the row's range, else null. */
export function parseValue(text, row) {
  const t = String(text).trim();
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return v >= row.min && v <= row.max ? v : null;
}

/** Validation text of a field ('' when fine or empty-and-untouched is not a case: every field has a value). */
export function validationText(text, row) {
  if (parseValue(text, row) !== null) return "";
  const hi = Number.isFinite(row.max) ? ` to ${row.max}` : " or more";
  return `allowed: ${row.min}${hi} ${row.unit}`;
}

/** The vehicle's value of a row from the MissionParams / MissionState fields (null = unknown). */
export function vehicleValue(row, params, mission) {
  const v = row.field === null ? mission && mission.stop_altitude : params && params[row.field];
  return finite(v) ? v : null;
}

/** 'vehicle: 7.0' or 'vehicle: unknown'. */
export const vehicleText = (v) => (finite(v) ? `vehicle: ${v.toFixed(1)}` : "vehicle: unknown");

/**
 * The rows to send: valid inputs that differ from the vehicle's value (or, with the vehicle value unknown, that the
 * user edited). `inputs` = {key: text}, `vehicle` = {key: number|null}, `dirty` = {key: bool}.
 * Returns {changed: [{row, value}], invalid: [keys]}.
 */
export function changedRows(inputs, vehicle, dirty = {}) {
  const changed = [];
  const invalid = [];
  for (const row of ROWS) {
    const v = parseValue(inputs[row.key] ?? "", row);
    if (v === null) {
      invalid.push(row.key);
      continue;
    }
    const cur = vehicle[row.key];
    if (finite(cur) ? Math.abs(cur - v) > TOL : !!dirty[row.key]) changed.push({ row, value: v });
  }
  return { changed, invalid };
}

/** Defaults as input texts. */
export const defaultInputs = () => Object.fromEntries(ROWS.map((r) => [r.key, String(r.def)]));

/** The apogee warning applies when the (valid or not yet valid) number in the field is below 90 m. */
export function apogeeWarning(text) {
  const t = String(text).trim();
  return /^\d+(\.\d+)?$/.test(t) && Number(t) < APOGEE_WARN_M ? APOGEE_WARNING : "";
}

/** Warning when |X| or |Y| of the apogee position (field texts) exceeds 0.5 m. */
export function lateralWarning(xText, yText) {
  const big = (t) => /^-?\d+(\.\d+)?$/.test(String(t).trim()) && Math.abs(Number(t)) > LATERAL_WARN_M;
  return big(xText) || big(yText) ? LATERAL_WARNING : "";
}

/** Plain-word result of one request of a row. `r` = request state from the core (null while sending the call). */
export function describeRow(row, r, value, vehicle, ackedFor = null) {
  if (row.action === "SET_STOP_ALTITUDE") return describeSetting(r, value, vehicle, ackedFor);
  if (!r) return { text: "sending", tone: "warn" };
  if (r.state === "sending") return { text: `sending (${r.attempts}/${r.max_attempts})`, tone: "warn" };
  if (r.state === "timeout") return { text: "no answer from the vehicle", tone: "bad" };
  if (r.state === "aborted") return { text: `aborted: ${r.abort_reason || "source disabled"}`, tone: "bad" };
  if (r.state !== "acked") return { text: "", tone: "info" };
  if (r.status_name === "DONE") return { text: "set", tone: "ok" };
  if (r.status_name === "IN_PROGRESS") return { text: "in progress", tone: "warn" };
  return { text: refusalText(r.status_name, r.detail), tone: "bad" };
}

/** One-line summary of the finished sequence from the rows' outcomes ('ok' | 'refused' | 'timeout' | 'error'). */
export function summarize(outcomes) {
  const n = outcomes.length;
  const count = (k) => outcomes.filter((o) => o === k).length;
  const ok = count("ok");
  if (ok === n) return n === 1 ? "1 parameter set" : `all ${n} parameters set`;
  const bad = [];
  if (count("refused")) bad.push(`${count("refused")} refused`);
  if (count("timeout")) bad.push(`${count("timeout")} without an answer`);
  if (count("error")) bad.push(`${count("error")} not sent`);
  return `${ok} of ${n} set, ${bad.join(", ")}`;
}

/**
 * Sends the changed rows one at a time. `send(row, value)` -> {req_id} (throws on a core refusal), `poll(reqId)` ->
 * the request's state object or null, `sleep(ms)`, `now()` in ms, `onUpdate(key, result)` with {text, tone, reqId,
 * final}. A new request (new req_id) per row; the next row starts GAP_MS after the previous one ended, also after a
 * refusal or timeout. `cancelled()` stops before the next row. Returns the outcomes in order.
 */
export async function runSequence(items, { send, poll, sleep, now, onUpdate, cancelled = () => false, pollMs = 100 }) {
  const outcomes = [];
  for (let i = 0; i < items.length; i++) {
    if (cancelled()) break;
    const { row, value } = items[i];
    if (i > 0) await sleep(GAP_MS);
    onUpdate(row.key, { ...describeRow(row, null, value, null), reqId: null, final: false });
    let reqId;
    try {
      reqId = (await send(row, value)).req_id;
    } catch (e) {
      onUpdate(row.key, { text: `not sent: ${e && e.message ? e.message : e}`, tone: "bad", reqId: null, final: true });
      outcomes.push("error");
      continue;
    }
    const t0 = now();
    let r = null;
    for (;;) {
      r = poll(reqId);
      if (r && (r.state === "acked" || r.state === "timeout" || r.state === "aborted") && r.status_name !== "IN_PROGRESS") break;
      if (now() - t0 > TIMEOUT_MS) {
        r = { state: "timeout" };
        break;
      }
      if (r) onUpdate(row.key, { ...describeRow(row, r, value, null), reqId, final: false });
      await sleep(pollMs);
    }
    const d = describeRow(row, r, value, null);
    onUpdate(row.key, { ...d, reqId, final: true, acked: r.state === "acked" && r.status_name === "DONE" });
    outcomes.push(r.state === "acked" ? (r.status_name === "DONE" ? "ok" : "refused") : r.state === "timeout" ? "timeout" : "error");
  }
  return outcomes;
}

/** Find the request with this id in a `requests` push (in flight first, else the recent list). */
export function findById(reqs, reqId) {
  if (!reqs || reqId === null || reqId === undefined) return null;
  const same = (r) => r.req_id === reqId;
  return (reqs.inflight || []).find(same) || (reqs.recent || []).find(same) || null;
}
