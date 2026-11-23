import test from "node:test";
import assert from "node:assert/strict";
import { compatibilityProblems, decodeFailureBits, decodeSensorState, describeVehicleStatus, hex, linkHealth } from "../app/status/status_logic.js";

const entry = (f, t = 100, extra = {}) => ({ type: "VehicleStatus", src: "usb", t, stale_after: 1, f, ...extra });
const FULL = {
  mode: "Ready", mission_mode: "Idle", mission_phase: "Idle", ctrl_source: "NONE", armed: false, simulation: true,
  motors_enabled: false, gs_present: true, uart_peer_seen: true, failsafe: false, failure_bits: 0, sensor_state: 0x2aa, last_req_id: 0, last_req_status: 0,
  rc_age: null,
};
const byKey = (d) => Object.fromEntries(d.items.map((i) => [i.key, i]));

test("failure bits decode to the flight software names, unknown bits are listed, raw stays", () => {
  assert.deepEqual(decodeFailureBits(0), []);
  assert.deepEqual(decodeFailureBits(0b100001), ["sensor", "control source lost"]);
  assert.deepEqual(decodeFailureBits(1 << 20), ["bit 20"]);
  const d = byKey(describeVehicleStatus(entry({ ...FULL, failure_bits: 33 }), 100.2));
  assert.match(d.failure_bits.text, /^33 \(0x21\): sensor, control source lost$/);
  assert.equal(d.failure_bits.tone, "bad");
  assert.match(byKey(describeVehicleStatus(entry(FULL), 100.2)).failure_bits.text, /^0 \(0x00\): none$/);
});

test("sensor_state decodes two bits per sensor and the raw value is shown", () => {
  assert.deepEqual(decodeSensorState(0b11_10).slice(0, 2), [{ name: "GNSS", state: "Ready" }, { name: "IMU", state: "Failure" }]);
  const d = byKey(describeVehicleStatus(entry(FULL), 100.2));
  assert.equal(d.sensor_state.text, "0x2AA");
  assert.match(d.sensor_state.title, /GNSS Ready/);
  assert.equal(hex(255), "0xFF");
});

test("simulation versus real sensors comes from VehicleStatus.simulation only, loud, never assumed", () => {
  let d = byKey(describeVehicleStatus(entry(FULL), 100.2));
  assert.deepEqual([d.simulation.text, d.simulation.tone, d.simulation.loud], ["SIMULATION", "sim", true]);
  d = byKey(describeVehicleStatus(entry({ ...FULL, simulation: false }), 100.2));
  assert.deepEqual([d.simulation.text, d.simulation.tone], ["REAL SENSORS", "real"]);
  d = byKey(describeVehicleStatus(entry({ ...FULL, simulation: undefined }), 100.2));
  assert.match(d.simulation.text, /not supplied/);
  assert.equal(d.simulation.tone, "");
});

test("motors cell: off / ARMED / ENABLED / enabled with gate closed, RUNNING tag, n/s", () => {
  const m = (over, thrust) => byKey(describeVehicleStatus(entry({ ...FULL, ...over }), 100.2, undefined, null, null, thrust)).motors;
  assert.deepEqual([m({ armed: false, motors_enabled: false }).text, m({ armed: false, motors_enabled: false }).tone], ["motors off", ""]);
  assert.deepEqual([m({ armed: true, motors_enabled: false }).text, m({ armed: true, motors_enabled: false }).tone], ["motors ARMED", "warn"]);
  assert.deepEqual([m({ armed: true, motors_enabled: true }).text, m({ armed: true, motors_enabled: true }).tone], ["motors ENABLED", "bad"]);
  assert.deepEqual([m({ armed: false, motors_enabled: true }).text, m({ armed: false, motors_enabled: true }).tone], ["motors enabled (gate closed)", "ok"]);
  assert.equal(m({ armed: true, motors_enabled: true }, 3).text, "motors ENABLED RUNNING");
  assert.equal(m({ armed: true, motors_enabled: true }, 3).tone, "bad");
  assert.equal(m({ armed: true, motors_enabled: true }, 0.01).text, "motors ENABLED");
  assert.equal(m({ armed: false, motors_enabled: false }, 3).text, "motors off RUNNING");
  assert.equal(m({ armed: undefined, motors_enabled: null }).text, "n/s");
  assert.equal(byKey(describeVehicleStatus(entry(FULL), 100.2)).armed, undefined);
});

test("armed, failsafe, gs_present, ctrl_source, rc_age, last request", () => {
  const d = byKey(
    describeVehicleStatus(entry({ ...FULL, armed: true, failsafe: true, gs_present: false, ctrl_source: "GS", ctrl_flags: 1 << 7, rc_age: 0.25, last_req_id: 7, last_req_status: 2 }), 100.2),
  );
  assert.equal(d.motors.text, "motors ARMED");
  assert.equal(d.failsafe.text, "FAILSAFE");
  assert.equal(d.gs_present.text, "GS absent");
  assert.equal(d.ctrl_source.text, "GS · USB fresh");
  assert.equal(d.rc_age.text, "0.25 s");
  assert.equal(d.rc_age.label, "Command age");
  assert.deepEqual([d.last_req.text, d.last_req.tone], ["id 7: REJECTED_STATE", "bad"]);
  assert.equal(byKey(describeVehicleStatus(entry({ ...FULL, rc_age: null }, 100, { nf: { rc_age: "nan" } }), 100.2)).rc_age.text, "none (NaN)");
  assert.equal(byKey(describeVehicleStatus(entry(FULL), 100.2)).last_req.text, "none (id 0)");
});

test("stale data is marked with its age, missing data says so", () => {
  const d = describeVehicleStatus(entry(FULL), 105);
  assert.equal(d.state, "stale");
  assert.equal(d.ageText, "5.0 s");
  assert.equal(describeVehicleStatus(undefined, 5).state, "missing");
  assert.equal(describeVehicleStatus(entry(FULL), 100.2).state, "fresh");
});

test("link health per source: connection state, rate, and a silent connected source is stale", () => {
  const stats = { usb: { t: 100, stale_after: 2, f: { rx_frames_s: 49.6 } }, link: { t: 90, stale_after: 2, f: { rx_frames_s: 10 } } };
  const h = linkHealth(
    [{ id: "usb", kind: "vehicle_usb", state: "connected" }, { id: "link", kind: "link_module", state: "connected" }, { id: "x", kind: "fake", state: "searching" }],
    (id) => stats[id], 101,
  );
  assert.deepEqual(h.map((x) => [x.id, x.tone, x.text, x.stale]), [["usb", "ok", "50 frames/s", false], ["link", "warn", "10 frames/s", true], ["x", "warn", "searching", false]]);
});

test("compatibility banner appears for a fresh mismatch and clears when it is gone or stale", () => {
  const bad = { src: "link", t: 10, stale_after: 2, f: { incompatible: true, detail: "node 1: schema hash 0x3AFA3CCE (GS 0x8D56E26D)" } };
  assert.deepEqual(compatibilityProblems([bad], 10.5), ["link: node 1: schema hash 0x3AFA3CCE (GS 0x8D56E26D)"]);
  assert.deepEqual(compatibilityProblems([{ ...bad, f: { incompatible: false, detail: "" } }], 10.5), []);
  assert.deepEqual(compatibilityProblems([bad], 20), []);
});

test("ship node UART chip: seen is ok, lost is amber (never red), not supplied is n/s", () => {
  const cell = (f) => describeVehicleStatus(entry(f), 100.2).items.find((i) => i.key === "uart_peer_seen");
  assert.deepEqual([cell(FULL).text, cell(FULL).tone], ["seen", "ok"]);
  assert.deepEqual([cell({ ...FULL, uart_peer_seen: false }).text, cell({ ...FULL, uart_peer_seen: false }).tone], ["lost", "warn"]);
  const { uart_peer_seen, ...without } = FULL;
  assert.deepEqual([cell(without).text, cell(without).tone], ["n/s", ""]);
});

test("ZEROING is shown only as the vehicle reports it, with the sent time as a plain record (no countdown)", () => {
  const z = (f, sent) => byKey(describeVehicleStatus(entry({ ...FULL, ...f }), 100.2, undefined, sent)).zeroing;
  assert.equal(z({ zeroing: true }).text, "ZEROING");
  assert.equal(z({ zeroing: true }).tone, "warn");
  assert.equal(z({ zeroing: true }).loud, true);
  assert.equal(z({ zeroing: true }, 4.2).text, "ZEROING (ZeroEstimators sent 4.2 s ago)");
  assert.equal(z({ zeroing: false }).text, "not zeroing");
  assert.equal(z({ zeroing: false }, 12).tone, "");
  assert.match(z({ zeroing: false }, 12).text, /^not zeroing \(ZeroEstimators sent/);
  assert.equal(z({}).text, "zeroing n/s"); // flag not supplied by the wire profile: never shown as false
  assert.equal(z({ zeroing: null }).tone, "");
  assert.ok(!/remaining|left|countdown/i.test(z({ zeroing: true }, 1).text));
});

test("T9b tail fields: nothing for zero or older senders, compact cells when set", () => {
  const none = byKey(describeVehicleStatus(entry({ ...FULL, ctrl_flags: 0, go_refusal: 0, degraded_bits: 0, mission_profile: 255, rc_rejected: 0 }), 100.2));
  for (const k of ["ctrl_flags", "go_refusal", "degraded", "mission_profile", "rc_rejected"]) assert.equal(none[k], undefined, k);
  const old = byKey(describeVehicleStatus(entry({ ...FULL, ctrl_flags: null, mission_profile: null }), 100.2));
  assert.equal(old.ctrl_flags, undefined);
  assert.equal(old.mission_profile, undefined);
  const d = byKey(describeVehicleStatus(entry({ ...FULL, ctrl_flags: 0b110, go_refusal: 6, degraded_bits: 0b101, mission_profile: 2, rc_rejected: 3 }), 100.2));
  assert.deepEqual([d.ctrl_flags.text, d.ctrl_flags.tone], ["ABORT latched, FAILSAFE latched", "bad"]);
  assert.equal(d.go_refusal.text, "reserved (motors armed is not a go precondition)");
  assert.equal(d.degraded.text, "ToF lost, ship-node heartbeat lost");
  assert.equal(d.mission_profile.text, "2");
  assert.equal(d.rc_rejected.text, "3");
  assert.equal(byKey(describeVehicleStatus(entry({ ...FULL, ctrl_flags: 0x180 }), 100.2)).ctrl_flags.tone, "");
  assert.deepEqual(decodeFailureBits(1 << 13), ["motors-armed watchdog"]);
  assert.deepEqual(decodeFailureBits(1 << 14), ["GS failsafe"]);
});
