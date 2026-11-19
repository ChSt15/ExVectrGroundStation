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
  "This stops the radio link of the TX/RX module and puts it into firmware-upload mode. The module always obeys, even in flight.";

export const actionKey = (a) => `${a.action}@${a.target}`;

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
  const same = (r) => r.action === a.action && r.target === a.target;
  return (reqs.inflight || []).find(same) || (reqs.recent || []).find(same) || null;
}

/** Text and tone of one request, from the core's report only. */
export function describeRequest(r) {
  if (!r) return { tone: "dim", text: "" };
  const id = `id ${r.req_id}`;
  if (r.state === "sending") return { tone: "warn", text: `sending, attempt ${r.attempts}/${r.max_attempts} via ${r.source || "?"} (${id})` };
  if (r.state === "timeout") return { tone: "bad", text: `TIMEOUT: no ACK after ${r.attempts} attempts (${id})` };
  if (r.state === "acked") {
    const ok = r.status_name === "DONE" || r.status_name === "IN_PROGRESS";
    return { tone: ok ? "ok" : "bad", text: `ACK ${r.status_name}${r.detail ? ` (detail ${r.detail})` : ""} (${id})` };
  }
  return { tone: "dim", text: String(r.state) };
}

/** Whether the send button can be used, and why not. The core still checks everything itself. */
export function sendState({ apiState, view, canControl, inflight, route }) {
  if (view !== "live") return { enabled: false, reason: "replay view: nothing is ever sent" };
  if (apiState !== "open") return { enabled: false, reason: "no connection to the core" };
  if (!canControl) return { enabled: false, reason: "this client is on a view-only host" };
  if (inflight) return { enabled: false, reason: "already in flight" };
  if (!route) return { enabled: false, reason: "no connected source can reach the target" };
  return { enabled: true, reason: "" };
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
