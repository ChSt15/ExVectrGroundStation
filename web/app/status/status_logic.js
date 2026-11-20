// Pure logic of the mission status bar (docs/gui.md). Vehicle-reported values only: nothing here is computed from
// what the ground station sent, and nothing is assumed. Stale data is marked, never hidden.
import { ageOf, fmtAge, fmtNum, isStale } from "../fmt.js";

/** failure_bits of VEHICLE_STATUS (flight-software docs/project/hardware-and-drivers.md, field mapping). */
export const FAILURE_BITS = [
  "sensor", "attitude", "position", "position out of bounds", "attitude out of bounds", "radio connection loss",
];

/** Sensors of `sensor_state`: 2 bits each, TelemetrySensor 0 Init, 1 Calib, 2 Ready, 3 Failure. */
export const SENSOR_FIELDS = ["GNSS", "IMU", "baro", "mag", "att KF", "pos KF"];
export const SENSOR_STATES = ["Init", "Calib", "Ready", "Failure"];
export const REQUEST_STATUS_NAMES = ["DONE", "IN_PROGRESS", "REJECTED_STATE", "REJECTED_INVALID", "FAILED"];

/** Names of the set failure bits; unknown bits are listed as "bit n". Returns [] for 0. */
export function decodeFailureBits(n) {
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return [];
  const out = [];
  for (let i = 0; i < 32; i++) {
    if (n & (1 << i)) out.push(FAILURE_BITS[i] || `bit ${i}`);
  }
  return out;
}

/** `sensor_state` decoded to [{name, state}] (display only: the raw number is always shown too). */
export function decodeSensorState(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return [];
  return SENSOR_FIELDS.map((name, i) => ({ name, state: SENSOR_STATES[(n >> (2 * i)) & 3] }));
}

export const hex = (n, width = 2) => (typeof n === "number" ? `0x${(n >>> 0).toString(16).toUpperCase().padStart(width, "0")}` : "—");

const MODE_TONE = { Running: "ok", Ready: "ok", Startup: "info", SensorInit: "info", SensorCalib: "info", Failure: "bad" };

function item(key, label, text, tone = "", extra = {}) {
  return { key, label, text, tone, ...extra };
}

/**
 * The cells of the vehicle status line from one `VehicleStatus` store entry (or undefined).
 * Returns { state: "missing"|"stale"|"fresh", age, items[] }. Booleans the wire profile did not supply
 * (null/undefined) are shown as "n/s", never as false.
 */
export function describeVehicleStatus(entry, now, schemaType) {
  if (!entry) return { state: "missing", age: null, items: [], src: "" };
  const f = entry.f || {};
  const stale = isStale(entry, now, schemaType);
  const age = ageOf(entry, now);
  const bool = (v, yes, no, yesTone, noTone = "") =>
    v === true ? [yes, yesTone] : v === false ? [no, noTone] : ["n/s", ""];
  const items = [];

  const [simText, simTone] =
    f.simulation === true ? ["SIMULATION", "sim"] : f.simulation === false ? ["REAL SENSORS", "real"] : ["SIM? (not supplied)", ""];
  items.push(item("simulation", "Sensors", simText, simTone, { loud: true }));

  items.push(item("mode", "Mode", f.mode === undefined ? "n/s" : String(f.mode), MODE_TONE[f.mode] || ""));
  items.push(item("mission", "Mission", f.mission_mode === undefined ? "n/s" : `${f.mission_mode} / ${f.mission_phase ?? "n/s"}`));

  const [armText, armTone] = bool(f.armed, "ARMED", "disarmed", "warn");
  items.push(item("armed", "Armed", armText, armTone));

  const [motText, motTone] = bool(f.motors_enabled, "MOTORS ENABLED", "motors off", "motors", "");
  items.push(item("motors_enabled", "Motors", motText, motTone, { loud: true }));

  const [fsText, fsTone] = bool(f.failsafe, "FAILSAFE", "no failsafe", "bad");
  items.push(item("failsafe", "Failsafe", fsText, fsTone));

  const bits = f.failure_bits;
  const names = decodeFailureBits(bits);
  const raw = typeof bits === "number" ? `${bits} (${hex(bits)})` : "n/s";
  items.push(item("failure_bits", "Failures", names.length ? `${raw}: ${names.join(", ")}` : typeof bits === "number" ? `${raw}: none` : raw, names.length ? "bad" : ""));

  const [gsText, gsTone] = bool(f.gs_present, "GS present", "GS absent", "ok", "warn");
  items.push(item("gs_present", "Ground station", gsText, gsTone));

  // information only: the vehicle heard the ship radio node on its UART in the last 3 s. Lost is amber, never red.
  const [uartText, uartTone] = bool(f.uart_peer_seen, "seen", "lost", "ok", "warn");
  items.push(item("uart_peer_seen", "Ship node UART", uartText, uartTone, { title: "VEHICLE_STATUS UART_PEER_SEEN: the vehicle received a valid frame from the ship radio node on its UART in the last 3 s (information only)" }));

  items.push(item("ctrl_source", "Control source", f.ctrl_source === undefined ? "n/s" : String(f.ctrl_source)));

  const rcAge = f.rc_age;
  const nfRc = entry.nf && entry.nf.rc_age;
  items.push(item("rc_age", "RC age", typeof rcAge === "number" ? `${fmtNum(rcAge, 2)} s` : nfRc === "nan" || rcAge === null ? "none (NaN)" : "n/s"));

  const rid = f.last_req_id;
  const rst = f.last_req_status;
  items.push(item("last_req", "Last request", rid === undefined ? "n/s" : rid === 0 ? "none (id 0)" : `id ${rid}: ${REQUEST_STATUS_NAMES[rst] ?? `#${rst}`}`, rid && rst !== 0 && rst !== 1 ? "bad" : ""));

  const ss = f.sensor_state;
  const dec = decodeSensorState(ss).map((s) => `${s.name} ${s.state}`).join(", ");
  items.push(item("sensor_state", "Sensor state (raw)", typeof ss === "number" ? hex(ss, 3) : "n/s", "", { title: dec }));

  return { state: stale ? "stale" : "fresh", age, items, src: entry.src, ageText: stale ? fmtAge(age) : "" };
}

/**
 * Health chip per source: connection state from the core's status, plus the age of its newest LinkStats
 * (receive rate packet) so that a connected but silent source shows as stale. The rate is `LinkStats.rx_frames_s`: frames
 * on the serial hop between the ground station and that device, NOT the radio rate (docs/gui.md "Data rates").
 */
export function linkHealth(sources, lookupLinkStats, now, schemaOf = () => undefined) {
  return (sources || []).map((s) => {
    const e = lookupLinkStats(s.id);
    let rate = null;
    let stale = false;
    let age = null;
    if (e) {
      age = ageOf(e, now);
      stale = isStale(e, now, schemaOf("LinkStats"));
      rate = e.f && typeof e.f.rx_frames_s === "number" ? e.f.rx_frames_s : null;
    }
    const tone = s.state === "connected" ? (e && stale ? "warn" : "ok") : s.state === "searching" ? "warn" : s.state === "error" ? "bad" : "";
    const text =
      s.state === "connected" ? (rate === null ? "connected" : `${Math.round(rate)} frames/s`)
        : s.state === "disabled" && s.disabled_by === "user" ? "DISABLED by user" : s.state;
    return { id: s.id, kind: s.kind, state: s.state, tone, text, stale: s.state === "connected" && !!e && stale, age };
  });
}

/** Schema or network version mismatch banners from the freshest NetworkStatus of each source. Clears itself. */
export function compatibilityProblems(entries, now, schemaType) {
  const out = [];
  for (const e of entries || []) {
    if (!e || !e.f || e.f.incompatible !== true) continue;
    if (isStale(e, now, schemaType)) continue; // a source that went quiet no longer asserts a mismatch
    out.push(`${e.src}: ${e.f.detail || "version mismatch"}`);
  }
  return out;
}
