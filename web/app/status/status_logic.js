// Pure logic of the mission status bar (docs/gui.md). Vehicle-reported values only: nothing here is computed from
// what the ground station sent, and nothing is assumed. Stale data is marked, never hidden.
import { ageOf, fmtAge, fmtNum, isStale } from "../fmt.js";
import { commandSourceCell } from "../command/command_logic.js";
import { missionIndicator } from "../command/profile.js";

/** Names of all defined failure_bits of VEHICLE_STATUS (flight-software docs/project/hardware-and-drivers.md, field
 * mapping): bit 5 is "control source lost" (T9b-3, was "radio connection loss"), bits 6-13 were added by T9b, bit 13 is the
 * motors-armed (interlock) watchdog and bit 14 the GS failsafe (a GS FAILSAFE request in an accepted CMD_STATE). Used by the status bar, the Health panel and (by length) the callouts. */
export const FAILURE_BIT_NAMES = [
  "sensor", "attitude", "position", "position out of bounds", "attitude out of bounds", "control source lost",
  "GNSS lost", "magnetometer lost", "barometer", "gyro", "accelerometer", "attitude estimate", "position estimate",
  "motors-armed watchdog", "GS failsafe",
];
export const FAILURE_BITS = FAILURE_BIT_NAMES;
/** Commanded thrust (N) above which the motors count as RUNNING. */
export const MOTORS_RUNNING_N = 0.05;

/** ctrl_flags of VEHICLE_STATUS (T9b command-path state). Bit index -> [short name, tone]. */
export const CTRL_FLAGS = [
  ["Toggle SD off, then on (motors armed)", "warn"], ["ABORT latched", "bad"], ["FAILSAFE latched", "bad"], ["no-position RTH", "warn"],
  ["SC mismatch", "warn"], ["SE hold zeroing", ""], ["SE refused", "warn"], ["USB fresh", ""], ["UART fresh", ""],
  ["source latched", ""], ["request queue full", "warn"], ["MULTI_MASTER", "bad"], ["GS failsafe request", "bad"],
];
/** go_refusal of VEHICLE_STATUS (vehicle docs hardware-and-drivers.md, T9b-4): the reason the last go edge (SA) was refused. */
export const GO_REFUSALS = [
  "none",
  "vehicle not Ready (or calibration invalid)",
  "failsafe latched (clear it with the SE short press)",
  "estimator zeroing window or no valid zero",
  "SC invalid, reserved or different from the vehicle mode",
  "SB invalid or profile not implemented (only SB 2 flies)",
  "reserved (motors armed is not a go precondition)",
  "calibration session or gyro run in progress",
];
/** Text for a go_refusal code (1-7); unknown codes are shown as "reason n". */
export const goRefusalText = (n) => GO_REFUSALS[n] ?? `reason ${n}`;
export const DEGRADED_BITS = ["ToF lost", "ship-node LINK_STATS lost", "ship-node heartbeat lost"];

/** Names of the set bits in `n` from `names`; unknown bits are "bit n". Not a number or 0: []. */
export function bitNames(n, names) {
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return [];
  const out = [];
  for (let i = 0; i < 32; i++) if (n & (1 << i)) out.push(names[i] || `bit ${i}`);
  return out;
}

/** Sensors of `sensor_state`: 2 bits each, TelemetrySensor 0 Init, 1 Calib, 2 Ready, 3 Failure. */
export const SENSOR_FIELDS = ["GNSS", "IMU", "baro", "mag", "att KF", "pos KF"];
export const SENSOR_STATES = ["Init", "Calib", "Ready", "Failure"];
export const REQUEST_STATUS_NAMES = ["DONE", "IN_PROGRESS", "REJECTED_STATE", "REJECTED_INVALID", "FAILED"];

/** Names of the set failure bits; unknown bits are listed as "bit n". Returns [] for 0. */
export function decodeFailureBits(n) {
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return [];
  const out = [];
  for (let i = 0; i < 32; i++) {
    if (n & (1 << i)) out.push(FAILURE_BIT_NAMES[i] || `bit ${i}`);
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
export function describeVehicleStatus(entry, now, schemaType, zeroSent = null, own16 = null, thrust = null, sel = null) {
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

  // ZEROING is the vehicle-reported flag only (VEHICLE_STATUS bit 7). "sent N s ago" is the ground station's own
  // record of the one frame it sent (zeroSent: seconds since, live view only); no countdown is computed or assumed.
  const sentText = typeof zeroSent === "number" ? ` (ZeroEstimators sent ${fmtAge(Math.max(0, zeroSent))} ago)` : "";
  const zeroTitle = "VEHICLE_STATUS ZEROING flag (bit 7): the vehicle reports it is zeroing its estimators. Shown exactly as reported; no countdown is computed here.";
  if (f.zeroing === true) items.push(item("zeroing", "Estimators", `ZEROING${sentText}`, "warn", { loud: true, title: zeroTitle }));
  else items.push(item("zeroing", "Estimators", f.zeroing === false ? `not zeroing${sentText}` : `zeroing n/s${sentText}`, "", { title: zeroTitle }));

  items.push(item("mode", "Mode", f.mode === undefined ? "n/s" : String(f.mode), MODE_TONE[f.mode] || ""));
  const mi = missionIndicator(f, sel && sel.applied, sel && sel.sent);
  items.push(item("mission_sel", "MISSION", mi.text, "", { mtag: mi.tag, mtone: mi.tone, title: mi.title }));
  items.push(item("mission", "Mission", f.mission_mode === undefined ? "n/s" : `${f.mission_mode} / ${f.mission_phase ?? "n/s"}`));

  // Motors, three states (owner decision 2026-11-23): ARMED = safety gate open (ARMED flag, SD), ENABLED = control software
  // may drive the motors (MOTORS_ENABLED), RUNNING = commanded thrust above MOTORS_RUNNING_N.
  const running = typeof thrust === "number" && Number.isFinite(thrust) && thrust > MOTORS_RUNNING_N;
  let motText = "n/s", motTone = "";
  if (f.armed === true || f.armed === false) {
    const armed = f.armed === true, en = f.motors_enabled === true;
    if (armed && en) [motText, motTone] = ["motors ENABLED", "bad"];
    else if (armed) [motText, motTone] = ["motors ARMED", "warn"];
    else if (en) [motText, motTone] = ["motors enabled (gate closed)", "ok"];
    else [motText, motTone] = ["motors off", ""];
    if (running) motText += " RUNNING";
  }
  items.push(item("motors", "Motors", motText, motTone, { loud: true, title: "Motors: ARMED = safety gate open (VEHICLE_STATUS ARMED flag: SD up, observed after SD down, with a fresh command state); ENABLED = the control software may drive the motors (MOTORS_ENABLED); RUNNING = commanded thrust above " + MOTORS_RUNNING_N + " N (Actuators)." }));

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

  // The command source (GS-control cutover): GS, the latched session, MULTI_MASTER, USB / UART fresh, GS_FAILSAFE_REQ, and NOT
  // MASTER when the latched session is not this ground station's (`own16` from the core's `rc` push).
  const cs = commandSourceCell(f, own16);
  items.push(item("ctrl_source", "Command source", cs.text, cs.tone, { title: cs.title }));

  // T9b tail fields, all vehicle-reported. Null/undefined = older sender: no cell. Zero or "none" values add nothing
  // alarming: the cells below appear only when something is set.
  const cf = f.ctrl_flags;
  if (typeof cf === "number" && cf > 0) {
    const names = CTRL_FLAGS.map((c, i) => (cf & (1 << i) ? c : null)).filter(Boolean);
    const extra = bitNames(cf & ~((1 << CTRL_FLAGS.length) - 1), []);
    const tone = names.some((c) => c[1] === "bad") ? "bad" : names.some((c) => c[1] === "warn") ? "warn" : "";
    items.push(item("ctrl_flags", "Command path", [...names.map((c) => c[0]), ...extra].join(", "), tone, { title: `VEHICLE_STATUS ctrl_flags ${hex(cf, 3)}` }));
  }
  if (typeof f.go_refusal === "number" && f.go_refusal > 0) {
    items.push(item("go_refusal", "Go refused", goRefusalText(f.go_refusal), "warn", { title: "VEHICLE_STATUS go_refusal: reason of the last refused go" }));
  }
  const degraded = bitNames(f.degraded_bits, DEGRADED_BITS);
  if (degraded.length) items.push(item("degraded", "Degraded", degraded.join(", "), "warn", { title: "VEHICLE_STATUS degraded_bits: warnings, no action taken" }));
  if (typeof f.rc_rejected === "number" && f.rc_rejected > 0) {
    items.push(item("rc_rejected", "Cmd rejected", String(f.rc_rejected), "", { title: "VEHICLE_STATUS rc_rejected: wrapping count of rejected CMD_STATE frames" }));
  }

  const rcAge = f.rc_age;
  const nfRc = entry.nf && entry.nf.rc_age;
  items.push(item("rc_age", "Command age", typeof rcAge === "number" ? `${fmtNum(rcAge, 2)} s` : nfRc === "nan" || rcAge === null ? "none (NaN)" : "n/s"));

  const he = f.home_epoch;
  items.push(item("home_epoch", "Home epoch", he === undefined ? "n/s" : he === 0 ? "no home yet (0)" : String(he), "", { title: "VEHICLE_STATUS home_epoch: increments whenever the vehicle's home reference changes (0 = none yet). A HOME_POSITION with this epoch, if one was received, is shown in the GNSS panel." }));

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
