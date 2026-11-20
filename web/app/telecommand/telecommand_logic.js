// Pure logic of the 'Commands (temporary)' panel (docs/telecommands.md). TEMPORARY, like the whole telecommand
// path: delete with the flight software's T9b.
// SAFETY-RELEVANT UI rules, enforced here and in telecommand_panel.js:
//  - Every send needs a hold of 1 s on the button, never anything automatic. One hold sends exactly ONE frame.
//  - There is no acknowledgement and no retry. The panel shows only "sent <time> ago, once"; what the command did
//    is whatever the status bar reports from the vehicle. SimulationMode TOGGLES on the vehicle, so the panel never
//    shows an assumed simulation state, only VehicleStatus.simulation as reported.
//  - Labels, groups, parameters and ranges come from the core's whitelist (`telecommand.list`).
import { HoldTimer } from "../radio/radio_logic.js";
import { fmtAge } from "../fmt.js";

export { HoldTimer };
export const TELECOMMAND_HOLD_MS = 1000;
export const NO_ACK_TEXT = "Sent once. There is no acknowledgement and no retry: check what the vehicle reports in the status bar.";

/** Whitelist rows grouped by their `group`, in the order the core lists them. */
export function groupCommands(commands) {
  const groups = [];
  for (const c of commands || []) {
    let g = groups.find((x) => x.group === c.group);
    if (!g) groups.push((g = { group: c.group, items: [] }));
    g.items.push(c);
  }
  return groups;
}

/** The newest send of this command from the latest `telecommands` push, or null. */
export function lastSent(push, name) {
  return ((push && push.recent) || []).find((r) => r.name === name) || null;
}

/** Text for the last send of a command, from the core's record only (no result is ever implied). */
export function describeSent(rec, now) {
  if (!rec) return { tone: "dim", text: "" };
  const ago = now === null || now === undefined ? "" : `${fmtAge(Math.max(0, now - rec.t))} ago`;
  return { tone: "info", text: `#${rec.n} sent ${ago} once via ${rec.source}, param ${rec.param_int}; no ACK exists` };
}

/** Whether the send button can be used, and why not. The core still checks everything itself. */
export function sendState({ apiState, view, canControl, route, paramOk = true }) {
  if (view !== "live") return { enabled: false, reason: "replay view: nothing is ever sent" };
  if (apiState !== "open") return { enabled: false, reason: "no connection to the core" };
  if (!canControl) return { enabled: false, reason: "this client is on a view-only host" };
  if (!route) return { enabled: false, reason: "no connected source reaches the vehicle" };
  if (!paramOk) return { enabled: false, reason: "parameter outside its allowed range" };
  return { enabled: true, reason: "" };
}

/** Parse a parameter field for an entry with a range: integer within [param_min, param_max], else null. */
export function parseParam(text, cmd) {
  if (cmd.param_min === null || cmd.param_min === undefined) return null;
  if (!/^-?\d+$/.test(String(text).trim())) return null;
  const v = Number(text);
  return v >= cmd.param_min && v <= cmd.param_max ? v : null;
}

/** The simulation state as the VEHICLE reports it (never an assumed one). entry: VehicleStatus store entry. */
export function reportedSimulation(entry) {
  if (!entry || !entry.f) return { text: "no VehicleStatus received", tone: "dim" };
  if (entry.f.simulation === true) return { text: "vehicle reports: SIMULATION", tone: "sim" };
  if (entry.f.simulation === false) return { text: "vehicle reports: REAL SENSORS", tone: "real" };
  return { text: "vehicle does not report the simulation state", tone: "dim" };
}
