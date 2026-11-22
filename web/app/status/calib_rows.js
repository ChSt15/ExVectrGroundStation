// Calibration rows of the Failures panel. Pure mapping to one row per sensor. Display only.
// Primary source: the vehicle's structured VehicleStatus.calib_state (decoded by the ground station into
// calib_gyro_ok / calib_accel_ok / calib_mag_ok / ready_to_arm; null = unknown, e.g. an older sender). The reason text
// and the fallback when the vehicle sends no calib_state come from the log watcher's calibration state (the vehicle's
// repeated "WARNING calibration invalid ..." line, callouts/logwatch.js).
export const CALIB_SENSORS = [
  { key: "gyro", label: "Gyro calibration" },
  { key: "accel", label: "Accel calibration" },
  { key: "mag", label: "Mag calibration" },
];

export const CALIB_REFUSAL_NOTE = "Arm and MissionBegin are refused until calibrated";

const isBool = (x) => typeof x === "boolean";

/**
 * `state` is LogWatcher.calibState(): {state: "unknown"|"ok"|"problem", detail}.
 * `status` is the fields of a fresh VehicleStatus (or null): when it carries calib_<key>_ok booleans they decide the
 * row (green/red) and the log only supplies the reason text; otherwise the log text decides alone.
 * Returns [{key, label, text, tone, note}] with tone "ok" (green), "bad" (red) or "" (grey, no information).
 */
export function calibRows(state, status = null) {
  return CALIB_SENSORS.map(({ key, label }) => {
    const sv = status ? status[`calib_${key}_ok`] : null;
    if (isBool(sv)) {
      if (sv) return { key, label, text: "ok", tone: "ok", note: "" };
      const why = state && state.detail && state.detail[key];
      return { key, label, text: "INVALID", tone: "bad", note: why && why !== "ok" ? why : "" };
    }
    if (!state || state.state === "unknown") return { key, label, text: "—", tone: "", note: "" };
    if (state.state === "ok") return { key, label, text: "ok", tone: "ok", note: "" };
    const why = state.detail && state.detail[key];
    if (why === undefined) return { key, label, text: "n/s", tone: "", note: "" }; // problem reported without per-sensor detail
    if (why === "ok") return { key, label, text: "ok", tone: "ok", note: "" };
    return { key, label, text: "INVALID", tone: "bad", note: why };
  });
}

/** The "Ready to arm" row from VehicleStatus.ready_to_arm (structured only; no log fallback). */
export function readyRow(status) {
  const label = "Ready to arm";
  const v = status ? status.ready_to_arm : null;
  if (!isBool(v)) return { key: "ready", label, text: "—", tone: "", note: "" };
  return v ? { key: "ready", label, text: "ready", tone: "ok", note: "" } : { key: "ready", label, text: "NOT ready", tone: "bad", note: "" };
}

export const calibAnyBad = (rows) => rows.some((r) => r.tone === "bad");
