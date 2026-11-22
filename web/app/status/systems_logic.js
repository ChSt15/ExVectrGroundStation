// Pure mapping of the Systems panel (docs/gui.md "Systems panel"): one entry per vehicle system (sensors, estimators,
// failure bits, failsafe, calibration, ready to arm) with a tone and a reason text (shown as the chip's tooltip).
// Display only; everything is derived from values the vehicle reported, nothing is judged by the ground station beyond
// the colour mapping below.
//
// tone: "ok" green = good, "warn" yellow = warning, "bad" red = failure, "" grey = no data (also stale data).
//  - sensor_state (2 bits per sensor): Ready -> ok, Init and Calib -> warn, Failure -> bad.
//  - GNSS: Failure bit -> bad; otherwise no fix (fix type below 2D, or 0 satellites) -> warn; otherwise as above.
//    No fix is never red on its own: only the vehicle's own GNSS failure is.
//  - failure bits and failsafe: set -> bad, clear -> ok. Stale or missing VehicleStatus -> grey.
//  - calibration rows and ready to arm: as calib_rows.js (ok / INVALID, ready / NOT ready).
import { FAILURE_BITS, decodeSensorState, hex } from "./status_logic.js";
import { calibRows, readyRow } from "./calib_rows.js";
import { fmtAge, fmtNum } from "../fmt.js";

export const SYSTEM_SENSORS = [
  { key: "imu", label: "IMU", field: "IMU" },
  { key: "baro", label: "baro", field: "baro" },
  { key: "mag", label: "mag", field: "mag" },
  { key: "gnss", label: "GNSS", field: "GNSS" },
  { key: "attkf", label: "att KF", field: "att KF" },
  { key: "poskf", label: "pos KF", field: "pos KF" },
];

const FAULT_LABEL = (n, i) => (i < 3 ? `${n} failure` : n);
const SENSOR_TONE = { Ready: "ok", Init: "warn", Calib: "warn", Failure: "bad" };
const MIN_FIX = 2; // 2D
const finite = (x) => typeof x === "number" && Number.isFinite(x);
const D = 180 / Math.PI;
const vec = (v, digits, k = 1) => (Array.isArray(v) ? v.map((x) => (finite(x) ? fmtNum(x * k, digits) : "?")).join(" / ") : null);

/** GNSS has no fix: fix type below 2D, or no satellites. null when the receiver data says nothing. */
export function gnssNoFix(gps) {
  if (!gps) return null;
  const hasFix = finite(gps.fix_type);
  const hasSats = finite(gps.num_sats);
  if (!hasFix && !hasSats) return null;
  return (hasFix && gps.fix_type < MIN_FIX) || (hasSats && gps.num_sats === 0);
}

function sensorTone(state) {
  return SENSOR_TONE[state] || "";
}

/**
 * @param {object} i
 *  status: VehicleStatus fields or null; statusState: "fresh" | "stale" | "missing" (only fresh data is coloured)
 *  gps: Gps fields or null (fresh only; pass null when stale); estimator / attitude / groundDistance: optional extra
 *  detail for the estimator tooltips; calib: LogWatcher.calibState() or null; lastSet: {bitIndex|"failsafe": viewTime}
 *  of the last time a failure was seen set; now: view time.
 * Returns [{ key, label, issue (short issue text for warn / bad, else ""), group: "sensor" | "estimator" | "failure" | "calib", tone, reason }].
 */
export function systemsStatus({ status = null, statusState = "missing", gps = null, estimator = null, attitude = null, groundDistance = null, calib = null, lastSet = {}, now = null } = {}) {
  const fresh = statusState === "fresh" && status ? status : null;
  const noData = statusState === "stale" ? "stale: no recent VehicleStatus" : "no VehicleStatus received";
  const ss = status && typeof status.sensor_state === "number" ? status.sensor_state : null;
  const dec = ss === null ? [] : decodeSensorState(ss);
  const out = [];

  for (const s of SYSTEM_SENSORS) {
    const group = s.key === "attkf" || s.key === "poskf" ? "estimator" : "sensor";
    const st = fresh && dec.length ? dec.find((x) => x.name === s.field) : null;
    let tone = "";
    let reason;
    if (!st) {
      reason = fresh ? "sensor_state not supplied" : noData;
    } else {
      tone = sensorTone(st.state);
      reason = `${st.state} (sensor_state ${hex(ss, 3)})`;
    }
    if (s.key === "gnss") {
      const nf = gnssNoFix(gps);
      const info = gps ? `fix ${finite(gps.fix_type) ? gps.fix_type : "?"}, ${finite(gps.num_sats) ? gps.num_sats : "?"} satellites` : "";
      if (st && st.state === "Failure") reason = `Failure: the vehicle reports a GNSS failure (sensor_state ${hex(ss, 3)})`;
      else if (nf === true) {
        tone = "warn";
        reason = `no fix (${info})`;
      } else if (nf === false && !st) {
        reason = `${reason}; receiver: ${info}`;
      } else if (st && info) reason = `${reason}; ${info}`;
    }
    if (s.key === "attkf") {
      const bits = [];
      const sig = estimator && vec(estimator.att_std, 2, D);
      if (sig) bits.push(`att σ N/W/U ${sig} °`);
      if (attitude && (finite(attitude.tilt_acc) || finite(attitude.north_acc))) bits.push(`tilt acc ${fmtNum((attitude.tilt_acc ?? NaN) * D, 2)} °, north acc ${fmtNum((attitude.north_acc ?? NaN) * D, 2)} °`);
      if (bits.length) reason += `; ${bits.join("; ")}`;
    }
    if (s.key === "poskf") {
      const bits = [];
      const v = estimator && vec(estimator.vel_std, 2);
      const p = estimator && vec(estimator.pos_std, 2);
      if (v) bits.push(`vel σ ${v} m/s`);
      if (p) bits.push(`pos σ ${p} m`);
      if (finite(groundDistance)) bits.push(`ground distance ${fmtNum(groundDistance, 2)} m`);
      if (bits.length) reason += `; ${bits.join("; ")}`;
    }
    let issue = "";
    if (tone === "warn" || tone === "bad") {
      if (s.key === "gnss" && st && st.state === "Failure") issue = "GNSS: Failure";
      else if (s.key === "gnss" && gnssNoFix(gps) === true) issue = "GNSS: no fix";
      else issue = `${s.label}: ${st ? st.state : reason}`;
    }
    out.push({ key: s.key, label: s.label, group, tone, issue, reason: `${s.label}: ${reason}` });
  }

  const failure = (key, label, set, lsKey) => {
    let tone = "";
    let reason = noData;
    if (fresh) {
      if (set === true) {
        tone = "bad";
        reason = "ACTIVE";
      } else if (set === false) {
        tone = "ok";
        reason = "ok";
      } else reason = "not supplied";
    }
    const t = lastSet[lsKey];
    if (t !== undefined && t !== null && now !== null && fresh) reason += `, last set ${fmtAge(now - t)} ago`;
    out.push({ key, label, group: "failure", tone, issue: tone === "bad" ? (key === "failsafe" ? "failsafe active" : label) : "", reason: `${label}: ${reason}` });
  };
  const fb = fresh && typeof fresh.failure_bits === "number" ? fresh.failure_bits : undefined;
  FAILURE_BITS.forEach((n, i) => failure(`b${i}`, FAULT_LABEL(n, i), fb === undefined ? undefined : (fb & (1 << i)) !== 0, i));
  failure("failsafe", "failsafe", fresh ? fresh.failsafe : undefined, "failsafe");

  const cr = calibRows(calib, fresh);
  const short = { gyro: "gyro cal", accel: "accel cal", mag: "mag cal" };
  for (const r of cr) {
    const reason = r.tone === "ok" ? "calibration valid" : r.tone === "bad" ? `calibration INVALID${r.note ? `: ${r.note}` : ""}` : "no calibration information";
    const issue = r.tone === "bad" ? `${short[r.key]}: ${r.note || "INVALID"}` : "";
    out.push({ key: `calib.${r.key}`, label: short[r.key], group: "calib", tone: r.tone, issue, reason: `${short[r.key]}: ${reason}` });
  }
  const rr = readyRow(fresh);
  out.push({ key: "calib.ready", label: "ready to arm", group: "calib", tone: rr.tone, issue: rr.tone === "bad" ? "not ready to arm" : "", reason: `ready to arm: ${rr.tone === "ok" ? "ready" : rr.tone === "bad" ? "NOT ready (refuses Arm and MissionBegin)" : "no information"}` });
  return out;
}

/**
 * Split items for the Health panel: issues (warn and bad, most severe first, order kept within a tone), good (ok) and
 * noData (grey).
 */
export function healthSections(items) {
  const issues = items.filter((x) => x.tone === "warn" || x.tone === "bad");
  issues.sort((a, b) => (a.tone === b.tone ? 0 : a.tone === "bad" ? -1 : 1));
  return { issues, good: items.filter((x) => x.tone === "ok"), noData: items.filter((x) => !x.tone) };
}

/** Counts of the tones, for the panel title: { ok, warn, bad, none }. */
export function systemsSummary(items) {
  const c = { ok: 0, warn: 0, bad: 0, none: 0 };
  for (const x of items) c[x.tone || "none"]++;
  return c;
}
