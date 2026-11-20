// Pure logic of the data-source banner and the top recorder block (docs/gui.md "Data-source banner").
// The banner says whether the data on the page is REAL (live, VehicleStatus.simulation false), SIMULATION (live,
// simulation true) or a REPLAY of a log (and what that recording was). Vehicle-reported values only: nothing is
// assumed from what the ground station sent; a missing, stale or unsupplied flag is shown as unknown.
import { ageOf, fmtAge, fmtBytes, fmtClock, isStale } from "../fmt.js";

export const LOCK_REPLAY = "replay: inputs locked";
export const LOCK_OFFLINE = "no core connection: inputs locked";

/** What the VehicleStatus entry of a view says about simulation: {sim: true|false|null, stale, age, why}. */
export function simulationFlag(entry, now, schemaType) {
  if (!entry) return { sim: null, stale: false, age: null, why: "no VehicleStatus received" };
  const f = entry.f || {};
  const age = ageOf(entry, now);
  const stale = isStale(entry, now, schemaType);
  if (f.simulation !== true && f.simulation !== false) return { sim: null, stale, age, why: `simulation flag not supplied by ${entry.src}` };
  return { sim: f.simulation, stale, age, why: "" };
}

/**
 * Banner state. `connState`: the GUI's WebSocket state; `view`: "live"|"replay"; `live`/`recorded`: results of
 * simulationFlag() for the live and the replay view; `replay`: status.replay of the core (or undefined).
 * Returns { kind: "real"|"sim"|"unknown"|"replay"|"offline", label, details[], recorded, inputsLocked, lockReason }.
 */
export function describeSource({ connState, view, live, recorded, replay }) {
  if (connState !== "open") {
    return { kind: "offline", label: "NO CORE CONNECTION", details: ["reconnecting", "values are frozen"], recorded: null, inputsLocked: true, lockReason: LOCK_OFFLINE };
  }
  if (view === "replay") {
    const r = replay || { state: "closed" };
    const details = [];
    let rec = null;
    if (r.state === "closed" || !r.name) {
      details.push("no log open");
    } else {
      details.push(r.name);
      const span = r.t_start != null && r.t_end != null ? r.t_end - r.t_start : null;
      if (r.state === "loading") details.push("loading (indexing)");
      else if (r.t != null && r.t_start != null) details.push(`${fmtClock(r.t - r.t_start)} / ${fmtClock(span)}`);
      if (r.state === "playing") details.push(`playing ${r.speed ?? 1}×`);
      else if (r.state === "paused") details.push("paused");
      else if (r.state === "ended") details.push("end of log");
      rec = !recorded || recorded.sim === null ? "unknown" : recorded.sim ? "SIMULATION" : "REAL";
    }
    return { kind: "replay", label: "REPLAY", details, recorded: rec, inputsLocked: true, lockReason: LOCK_REPLAY };
  }
  const l = live || { sim: null, why: "no VehicleStatus received" };
  if (l.sim === null) return { kind: "unknown", label: "LIVE · REAL OR SIMULATION UNKNOWN", details: [l.why], recorded: null, inputsLocked: false, lockReason: "" };
  const what = l.sim ? "SIMULATION" : "REAL";
  if (l.stale) {
    return { kind: "unknown", label: `LIVE · last reported ${what}`, details: [`VehicleStatus stale ${fmtAge(l.age)}`], recorded: null, inputsLocked: false, lockReason: "" };
  }
  return l.sim
    ? { kind: "sim", label: "LIVE · SIMULATION", details: ["the vehicle runs its on-board simulation"], recorded: null, inputsLocked: false, lockReason: "" }
    : { kind: "real", label: "LIVE · REAL VEHICLE", details: ["real sensors and actuators"], recorded: null, inputsLocked: false, lockReason: "" };
}

export const FREE_WARN_BYTES = 5e9;
export const FREE_BAD_BYTES = 1e9;

/** Top recorder block from status.recorder and the core clock. */
export function describeRecorder(rec, clock) {
  if (!rec || rec.available === false) return { available: false, active: false, text: "recorder not available", tone: "", error: "" };
  const free = typeof rec.free_bytes === "number" ? rec.free_bytes : null;
  const freeTone = free === null ? "" : free < FREE_BAD_BYTES ? "bad" : free < FREE_WARN_BYTES ? "warn" : "";
  const out = {
    available: true,
    active: !!rec.active,
    name: rec.name || "",
    duration: rec.active && rec.started && typeof clock === "number" ? fmtClock(clock - rec.started) : "",
    size: rec.active ? fmtBytes(rec.bytes ?? 0) : "",
    free: free === null ? "" : `${fmtBytes(free)} free`,
    freeTone,
    error: rec.error ? String(rec.error) : "",
    dropped: rec.dropped || 0,
  };
  out.tone = out.error ? "bad" : out.active ? "rec" : "";
  return out;
}
