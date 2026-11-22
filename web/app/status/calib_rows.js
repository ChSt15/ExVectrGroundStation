// Calibration rows of the Failures panel. Pure mapping from the log watcher's calibration state (the vehicle's repeated
// "WARNING calibration invalid ..." line, callouts/logwatch.js) to one row per sensor. Display only.
export const CALIB_SENSORS = [
  { key: "gyro", label: "Gyro calibration" },
  { key: "accel", label: "Accel calibration" },
  { key: "mag", label: "Mag calibration" },
];

export const CALIB_REFUSAL_NOTE = "Arm and MissionBegin are refused until calibrated";

/**
 * `state` is LogWatcher.calibState(): {state: "unknown"|"ok"|"problem", detail}.
 * Returns [{key, label, text, tone, note}] with tone "ok" (green), "bad" (red) or "" (grey, no information).
 */
export function calibRows(state) {
  return CALIB_SENSORS.map(({ key, label }) => {
    if (!state || state.state === "unknown") return { key, label, text: "—", tone: "", note: "" };
    if (state.state === "ok") return { key, label, text: "ok", tone: "ok", note: "" };
    const why = state.detail && state.detail[key];
    if (why === undefined) return { key, label, text: "n/s", tone: "", note: "" }; // problem reported without per-sensor detail
    if (why === "ok") return { key, label, text: "ok", tone: "ok", note: "" };
    return { key, label, text: "INVALID", tone: "bad", note: why };
  });
}

export const calibAnyBad = (rows) => rows.some((r) => r.tone === "bad");
