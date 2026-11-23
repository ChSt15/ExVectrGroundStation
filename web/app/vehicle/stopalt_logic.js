// Pure logic of the 'Stop altitude' setting (docs/requests.md). Display and command only,
// nothing sends it automatically. It is the whitelisted REQUEST SET_STOP_ALTITUDE (action 13, arg_f = metres >= 1,
// no maximum): the vehicle answers with a REQUEST_ACK, and the request layer of the core retries (same req_id) and
// ends as timeout. 'set' needs BOTH the ACK DONE and the echoed MissionState.stop_altitude matching (cross-check).
import { refusalText } from "../requests/requests_logic.js";

export const ACTION = "SET_STOP_ALTITUDE";
export const TARGET = 1;
export const MIN_M = 1;
export const MAX_M = Infinity; // no maximum, the value only has to be finite
export const DEFAULT_M = 20;
export const TOLERANCE_M = 0.05;
export const ECHO_WAIT_S = 4; // after an ACK DONE the echo may take a few status periods
export const ACCEPT_NOTE = "The vehicle accepts it only on the ground with the motors disarmed and only from this ground station's command session; otherwise it answers with the reason and the value stays.";

const finite = (x) => typeof x === "number" && Number.isFinite(x);

/** The field text as metres within [min, max], else null. */
export function parseStopAltitude(text, min = MIN_M, max = MAX_M) {
  const t = String(text).trim();
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return v >= min && v <= max ? v : null;
}

/** 'vehicle: 20.0 m' or 'vehicle: unknown' from the echoed value. */
export const echoText = (echo) => (finite(echo) ? `vehicle: ${echo.toFixed(1)} m` : "vehicle: unknown");

/** Find the request with this id in a `requests` push (in flight first, else the recent list). */
export function findRequest(reqs, reqId) {
  if (!reqs || reqId === null || reqId === undefined) return null;
  const same = (r) => r.req_id === reqId && r.action === ACTION;
  return (reqs.inflight || []).find(same) || (reqs.recent || []).find(same) || null;
}

/**
 * Chip text and tone of one setting attempt. `r` = the request state from the core (null while the send call is
 * still being answered), `target` = the metres asked for, `echo` = MissionState.stop_altitude, `ackedFor` = seconds
 * since the ACK arrived (or null).
 */
export function describeSetting(r, target, echo, ackedFor = null) {
  if (!r) return { text: "sending", tone: "warn" };
  if (r.state === "sending") return { text: `sending (${r.attempts}/${r.max_attempts})`, tone: "warn" };
  if (r.state === "timeout") return { text: "no answer from the vehicle", tone: "bad" };
  if (r.state === "aborted") return { text: `aborted: ${r.abort_reason || "source disabled"}`, tone: "bad" };
  if (r.state !== "acked") return { text: "", tone: "info" };
  if (r.status_name === "DONE") {
    if (finite(echo) && Math.abs(echo - target) <= TOLERANCE_M) return { text: "set", tone: "ok" };
    if (ackedFor !== null && ackedFor > ECHO_WAIT_S) return { text: `acknowledged, but the vehicle reports ${echoText(echo).slice(9)}`, tone: "bad" };
    return { text: "acknowledged, waiting for the echo", tone: "warn" };
  }
  if (r.status_name === "IN_PROGRESS") return { text: "in progress", tone: "warn" };
  return { text: refusalText(r.status_name, r.detail), tone: "bad" };
}

/** The error text of a core refusal of the send call itself (no route, rate limit, in flight, ...). */
export const sendErrorText = (e) => `Stop altitude not sent: ${e && e.message ? e.message : e}`;
