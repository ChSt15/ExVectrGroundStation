// Pure logic of the raw sensor panel (docs/gui.md). Raw (uncalibrated) ImuRaw values as the vehicle sends them over
// its USB debug set, and the "calibrated (computed here with the ship's formulas)" column computed from the
// vehicle's CALIBRATION packet (flight-software docs/project/hardware-and-drivers.md section 8a):
//   cal = M * (raw - offset), M row-major (M[r][c] = matrix[3r + c]), SI units
//   omega_est = gyro_cal - gyro_bias_est
// DISPLAY ONLY: nothing computed here is sent, stored, or used for anything else. Without a CALIBRATION packet
// the column shows a placeholder and nothing is guessed.

export const WINDOW_S = 10; // sparkline history

/** One row per quantity: scale and unit are display only (the packet is SI: rad/s, m/s^2, T, Pa, K). */
export const QUANTITIES = [
  { key: "gyro", label: "Gyro", field: "gyro", unit: "rad/s", scale: 1, digits: 4, vec: true, magnitude: false },
  { key: "accel", label: "Accelerometer", field: "accel", unit: "m/s²", scale: 1, digits: 3, vec: true, magnitude: true },
  { key: "mag", label: "Magnetometer", field: "mag", unit: "µT", scale: 1e6, digits: 2, vec: true, magnitude: true },
  { key: "pressure", label: "Pressure", field: "pressure", unit: "Pa", scale: 1, digits: 0, vec: false, magnitude: false },
  { key: "temperature", label: "Temperature", field: "temperature", unit: "K", scale: 1, digits: 2, vec: false, magnitude: false },
];

/** Calibrated counterparts: key of the computed result, unit/scale for display (mag shown in µT). */
export const CALIBRATED_ROWS = {
  gyro: { key: "gyro", unit: "rad/s", scale: 1, digits: 4 },
  accel: { key: "accel", unit: "m/s²", scale: 1, digits: 3 },
  mag: { key: "mag", unit: "µT", scale: 1e6, digits: 2 },
};

export const CALIBRATED_PLACEHOLDER = "waiting for CALIBRATION packet";

/** Row-major 3x3 matrix (9 numbers) times a 3-vector; undefined if anything is not a finite number. */
export function matVec(m, v) {
  if (!Array.isArray(m) || m.length !== 9 || !Array.isArray(v) || v.length !== 3) return undefined;
  if (m.some((x) => typeof x !== "number") || v.some((x) => typeof x !== "number")) return undefined;
  return [0, 1, 2].map((r) => m[3 * r] * v[0] + m[3 * r + 1] * v[1] + m[3 * r + 2] * v[2]);
}

/** cal = M * (raw - offset). Components that are null/NaN on the wire give undefined (never a guess). */
export function calibrate(raw, offset, matrix) {
  if (!Array.isArray(raw) || !Array.isArray(offset) || raw.length !== 3 || offset.length !== 3) return undefined;
  if (raw.some((x) => typeof x !== "number") || offset.some((x) => typeof x !== "number")) return undefined;
  return matVec(matrix, [raw[0] - offset[0], raw[1] - offset[1], raw[2] - offset[2]]);
}

/**
 * Calibrated gyro, accel, mag and omega_est from the f dicts of an ImuRaw and a Calibration packet.
 * Each entry is undefined where an input is missing. `raw_frame` other than 0 (SENSOR) is not computed.
 */
export function computeCalibrated(imu, cal) {
  const out = { gyro: undefined, accel: undefined, mag: undefined, omega_est: undefined };
  if (!imu || !cal) return out;
  if (cal.raw_frame !== undefined && cal.raw_frame !== null && cal.raw_frame !== 0) return out;
  out.gyro = calibrate(imu.gyro, cal.gyro_offset, cal.gyro_matrix);
  out.accel = calibrate(imu.accel, cal.accel_offset, cal.accel_matrix);
  out.mag = calibrate(imu.mag, cal.mag_offset, cal.mag_matrix);
  const b = cal.gyro_bias_est;
  if (out.gyro && Array.isArray(b) && b.length === 3 && b.every((x) => typeof x === "number")) out.omega_est = out.gyro.map((x, i) => x - b[i]);
  return out;
}

/** Bits of `*_source` (schema CALIBRATION): where the vehicle's values came from. */
export const SOURCE_BITS = [
  [0, "calib EEPROM"],
  [1, "transform EEPROM"],
  [2, "offset override (hard-coded)"],
  [3, "RAM only (not saved)"],
  [4, "memory cleared (defaults)"],
];

/** Decoded source flags of one sensor, e.g. "calib EEPROM, transform EEPROM"; "identity defaults" when no bit is set. */
export function decodeSource(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "n/s";
  const names = SOURCE_BITS.filter(([bit]) => n & (1 << bit)).map(([, name]) => name);
  return names.length ? names.join(", ") : "identity defaults";
}

/** Euclidean norm of a 3-vector, or undefined when any component is not a finite number. */
export function magnitude(v) {
  if (!Array.isArray(v) || v.length !== 3 || v.some((x) => typeof x !== "number" || !Number.isFinite(x))) return undefined;
  return Math.hypot(v[0], v[1], v[2]);
}

/** Scalar that gets the sparkline of a quantity: the magnitude for vectors, else the value itself. */
export function sparkValue(q, value) {
  if (q.vec) return magnitude(value);
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** Whether any vehicle USB source is connected (the panel only has data then). */
export function vehicleUsbConnected(status) {
  return !!status && (status.sources || []).some((s) => s.kind === "vehicle_usb" && s.state === "connected");
}

/** Rolling time series for a sparkline, pruned to `windowS` of view time. */
export class Series {
  constructor(windowS = WINDOW_S) {
    this.windowS = windowS;
    this.pts = []; // [t, v]
  }

  add(t, v) {
    if (typeof v !== "number" || !Number.isFinite(v)) return;
    const last = this.pts[this.pts.length - 1];
    if (last && t < last[0]) this.pts = []; // time went backwards (replay seek): start over
    this.pts.push([t, v]);
    const cut = t - this.windowS;
    while (this.pts.length > 2 && this.pts[0][0] < cut) this.pts.shift();
  }

  clear() {
    this.pts = [];
  }

  /** SVG polyline `points` for a w x h box, auto-scaled to the window's min and max (flat line when constant). */
  path(w, h) {
    if (this.pts.length < 2) return "";
    const t1 = this.pts[this.pts.length - 1][0];
    const t0 = t1 - this.windowS;
    let lo = Infinity;
    let hi = -Infinity;
    for (const [, v] of this.pts) {
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
    const span = hi - lo || 1;
    return this.pts
      .map(([t, v]) => `${(((t - t0) / this.windowS) * w).toFixed(1)},${(h - 1 - ((v - lo) / span) * (h - 2)).toFixed(1)}`)
      .join(" ");
  }
}
