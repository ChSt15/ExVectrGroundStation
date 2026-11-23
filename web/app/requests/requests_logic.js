// Pure logic of the Requests panel (docs/requests.md), kept free of the DOM so it is unit tested.
// SAFETY-RELEVANT UI rules, enforced here and in requests_panel.js:
//  - The panel shows the state the core reports (`requests` pushes, which come from REQUEST_ACKs only): sending,
//    ACK with the status exactly as the vehicle reported it, or timeout. Nothing is shown as done because it was sent.
//  - Every send needs a hold of 1 s on the button. A `danger` action also needs a modal whose confirm button stays
//    disabled for 2 s. The GUI never sends anything by itself, only from these deliberate gestures.
//  - The panel does not know what an action means; labels, groups, targets and confirm levels come from the core's
//    whitelist (`request.list`).
import { HoldTimer, realTimers } from "../radio/radio_logic.js";

export { HoldTimer };
export const REQUEST_HOLD_MS = 1000; // hold the button this long to send
export const DANGER_ENABLE_MS = 2000; // the modal's confirm button is disabled for this long
export const DANGER_TEXT =
  "This acts on a link module (the Router or the ship radio node): its radio link stops or restarts. The module always obeys, even in flight.";

/** Warning text of a `danger` whitelist row: its own `warning` from the config, else the generic text. */
export const dangerText = (a) => (a && a.warning) || DANGER_TEXT;

/** Row key: action and target, plus the fixed arg_i for rows that share an action (CALIB_CONTROL codes, CALIB_APPLY subsystems). */
export const actionKey = (a) => `${a.action}@${a.target}${!a.arg_i_range && a.arg_i ? `#${a.arg_i}` : ""}`;

/** Whitelist rows grouped by their `group`, in the order the core lists them. */
export function groupActions(actions) {
  const groups = [];
  for (const a of actions || []) {
    let g = groups.find((x) => x.group === a.group);
    if (!g) groups.push((g = { group: a.group, items: [] }));
    g.items.push(a);
  }
  return groups;
}

/** The request of the latest `requests` push that belongs to this whitelist row: in flight first, else newest finished. */
export function requestFor(reqs, a) {
  if (!reqs) return null;
  const same = (r) => r.action === a.action && r.target === a.target && (a.arg_i_range || (r.arg_i ?? 0) === (a.arg_i ?? 0));
  return (reqs.inflight || []).find(same) || (reqs.recent || []).find(same) || null;
}

/**
 * Plain words for a vehicle refusal (REQUEST_ACK status name and detail), or "" for DONE / IN_PROGRESS. The codes
 * are the vehicle's (workspace flight-software request executor); the GS only words them.
 */
export function refusalText(statusName, detail) {
  const d = Number(detail) || 0;
  if (statusName === "DONE" || statusName === "IN_PROGRESS") return "";
  if (statusName === "REJECTED_STATE") {
    if (d === 1) return "refused: motors armed or in flight";
    if (d === 2) return "refused: the estimators cannot be zeroed in the vehicle's current state";
    if (d === 3) return "refused: session mismatch, restart the GS or wait 1 s";
    if (d === 4) return "refused: the vehicle has no home position yet";
    if (d === 5) return "refused: the vehicle is not in simulation mode";
    if (d === 6) return "refused: calibration refused, see the console";
    return `refused: vehicle state does not allow it (detail ${d})`;
  }
  if (statusName === "REJECTED_INVALID") {
    if (d === 1) return "refused: value out of range";
    if (d === 2) return "refused: wrong target";
    if (d === 3) return "refused: unknown action (vehicle software too old?)";
    return `refused: invalid request (detail ${d})`;
  }
  if (statusName === "FAILED") return "failed on the vehicle";
  return `unknown answer ${statusName}`;
}

/** An integer argument field as an integer within [min, max] of the row's `arg_i_range`, else null. */
export function parseArgI(text, range) {
  if (!range) return null;
  const t = String(text).trim();
  if (!/^-?\d+$/.test(t)) return null;
  const v = Number(t);
  return v >= range.min && v <= range.max ? v : null;
}

/** Newest request (in flight first) of any of these actions in a `requests` push, or null. */
export function latestOf(reqs, actions) {
  if (!reqs) return null;
  const same = (r) => actions.includes(r.action);
  return (reqs.inflight || []).find(same) || (reqs.recent || []).find(same) || null;
}

/** The home position as the vehicle reported it (HomePosition entry of the store), in plain words. */
export function homeText(entry) {
  const f = entry && entry.f;
  if (!f) return { tone: "dim", text: "no home position received (press 'Request home position')" };
  const deg = (r) => ((r * 180) / Math.PI).toFixed(6);
  const ll = f.latlon_valid && Number.isFinite(f.lat) && Number.isFinite(f.lon) ? `${deg(f.lat)}, ${deg(f.lon)}` : "no lat/lon";
  const alt = f.alt_valid && Number.isFinite(f.alt) ? `${f.alt.toFixed(1)} m` : "no altitude";
  return { tone: "info", text: `home: ${ll}, ${alt}${f.simulation ? " (simulation)" : ""}, epoch ${f.epoch}` };
}

/**
 * Send a sequence of requests one after the other, waiting for each ACK (the vehicle's inbox holds 8). Each step
 * `{action, label, arg_i?, arg_f?}` is sent with `send(step)` (returns the core's request state), then `status()`
 * (the core's request.status) is polled every `pollMs` until it is no longer `sending` or `timeoutMs` pass; the next
 * step follows `spacingMs` later (the core allows one new request per 500 ms per client). The sequence stops at the
 * first refusal, error or timeout. Returns {done, error: null | {step, text}}; `sleep(ms)` is injectable for tests.
 */
export async function runRequestSequence(steps, { send, status, sleep, spacingMs = 550, pollMs = 200, timeoutMs = 7000 }) {
  let done = 0;
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    if (i > 0) await sleep(spacingMs);
    let st;
    try {
      st = await send(step);
    } catch (e) {
      return { done, error: { step, text: `${step.label || step.action} not sent: ${e && e.message ? e.message : e}` } };
    }
    let waited = 0;
    let fin = null;
    while (waited <= timeoutMs) {
      const s = await status();
      const r = [...(s.inflight || []), ...(s.recent || [])].find((x) => x.req_id === st.req_id && x.action === step.action);
      if (r && r.state !== "sending") { fin = r; break; }
      await sleep(pollMs);
      waited += pollMs;
    }
    const d = describeRequest(fin || { state: "timeout", attempts: st.attempts, req_id: st.req_id });
    if (!fin || d.tone !== "ok") return { done, error: { step, text: `${step.label || step.action}: ${d.text}` } };
    done++;
  }
  return { done, error: null };
}

/** Text and tone of one request, from the core's report only. */
export function describeRequest(r) {
  if (!r) return { tone: "dim", text: "" };
  const id = `id ${r.req_id}`;
  if (r.state === "sending") return { tone: "warn", text: `sending, attempt ${r.attempts}/${r.max_attempts} via ${r.source || "?"} (${id})` };
  if (r.state === "aborted") return { tone: "bad", text: `ABORTED after ${r.attempts} attempt(s): ${r.abort_reason || "aborted"} (${id})` };
  if (r.state === "timeout") return { tone: "bad", text: `TIMEOUT: no ACK after ${r.attempts} attempts (${id})` };
  if (r.state === "acked") {
    const ok = r.status_name === "DONE" || r.status_name === "IN_PROGRESS";
    const why = refusalText(r.status_name, r.detail);
    return { tone: ok ? "ok" : "bad", text: `ACK ${r.status_name}${r.detail ? ` (detail ${r.detail})` : ""}${why ? `: ${why}` : ""} (${id})` };
  }
  return { tone: "dim", text: String(r.state) };
}

/** Whether the send button can be used, and why not. The core still checks everything itself. */
export function sendState({ apiState, view, canControl, inflight, route, argOk = true }) {
  if (view !== "live") return { enabled: false, reason: "replay view: nothing is ever sent" };
  if (apiState !== "open") return { enabled: false, reason: "no connection to the core" };
  if (!canControl) return { enabled: false, reason: "this client is on a view-only host" };
  if (inflight) return { enabled: false, reason: "already in flight" };
  if (!route) return { enabled: false, reason: "no connected source can reach the target" };
  if (!argOk) return { enabled: false, reason: "argument outside its allowed range" };
  return { enabled: true, reason: "" };
}

/** An action's float argument field as a number within [min, max] of the whitelist row's `arg_f`, else null. */
export function parseArgF(text, spec) {
  if (!spec) return null;
  const t = String(text).trim();
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return v >= spec.min && v <= spec.max ? v : null;
}

/** The deliberate gate of a `danger` action: `ready()` becomes true 2 s after `open()`. */
export class DangerGate {
  constructor(timers = realTimers, enableMs = DANGER_ENABLE_MS) {
    this.t = timers;
    this.enableMs = enableMs;
    this.openedAt = null;
  }

  open() {
    this.openedAt = this.t.now();
  }

  close() {
    this.openedAt = null;
  }

  get isOpen() {
    return this.openedAt !== null;
  }

  remainingMs() {
    return this.openedAt === null ? this.enableMs : Math.max(0, this.enableMs - (this.t.now() - this.openedAt));
  }

  ready() {
    return this.openedAt !== null && this.remainingMs() === 0;
  }
}

/** The simulation state as the VEHICLE reports it (never an assumed one). entry: VehicleStatus store entry. */
export function reportedSimulation(entry) {
  if (!entry || !entry.f) return { text: "no VehicleStatus received", tone: "dim" };
  if (entry.f.simulation === true) return { text: "vehicle reports: SIMULATION", tone: "sim" };
  if (entry.f.simulation === false) return { text: "vehicle reports: REAL SENSORS", tone: "real" };
  return { text: "vehicle does not report the simulation state", tone: "dim" };
}
