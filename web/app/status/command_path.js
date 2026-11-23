// Pure: the vehicle's own command-path state (VEHICLE_STATUS, T9b-4) as rows for the virtual radio panel and the
// Simulation testing page. Vehicle-reported values only; nothing is assumed from what the ground station sent.
import { profileText } from "../command/profile.js";
import { CTRL_FLAGS, goRefusalText } from "./status_logic.js";

/** Shown while the vehicle reports no countdown length (the go countdown is compiled into the vehicle, not sent). */
export const COUNTDOWN_NOTE = "about 15 s";
/** ctrl_flags bits shown on the radio panel: bit -> label. Bit numbers are the schema's. */
const SHOWN = [[0, "bad"], [1, "bad"], [2, "bad"], [4, "warn"], [5, ""], [6, "warn"], [11, "bad"], [12, "bad"]];

/** @returns {{key: string, label: string, text: string, tone: string}[]} rows; [] when no VehicleStatus is known. */
export function commandPathRows(entry) {
  const f = entry && entry.f;
  if (!f) return [];
  const rows = [];
  const row = (key, label, text, tone = "") => rows.push({ key, label, text, tone });
  row("mode", "Vehicle mode", f.simulation === true ? "SIMULATION" : f.simulation === false ? "REAL SENSORS" : "n/s", f.simulation === true ? "sim" : "");
  row("interlock", "Motors", f.armed === true ? "MOTORS ARMED" : f.armed === false ? "disarmed" : "n/s", f.armed === true ? "warn" : "");
  const cf = typeof f.ctrl_flags === "number" ? f.ctrl_flags : 0;
  const names = [];
  let tone = "";
  for (const [bit, t] of SHOWN) {
    if (!(cf & (1 << bit))) continue;
    names.push(CTRL_FLAGS[bit][0]);
    if (t === "bad" || (t === "warn" && tone !== "bad")) tone = t;
  }
  row("flags", "Command path", names.length ? names.join(", ") : "nothing pending", tone);
  if (typeof f.go_refusal === "number" && f.go_refusal > 0) row("go_refusal", "Go refused", goRefusalText(f.go_refusal), "warn");
  if (typeof f.mission_profile === "number" && f.mission_profile !== 255) row("mission_profile", "Profile (SB at go)", profileText(f.mission_profile));
  row("countdown", "Go countdown", COUNTDOWN_NOTE);
  return rows;
}
