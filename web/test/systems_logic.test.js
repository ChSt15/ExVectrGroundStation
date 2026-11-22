import test from "node:test";
import assert from "node:assert/strict";
import { gnssNoFix, systemsStatus, systemsSummary } from "../app/status/systems_logic.js";

const bySys = (items) => Object.fromEntries(items.map((x) => [x.key, x]));
// sensor_state: 2 bits per sensor in the order GNSS, IMU, baro, mag, att KF, pos KF (0 Init, 1 Calib, 2 Ready, 3 Failure)
const ss = (gnss, imu, baro, mag, att, pos) => gnss | (imu << 2) | (baro << 4) | (mag << 6) | (att << 8) | (pos << 10);
const ALLREADY = ss(2, 2, 2, 2, 2, 2);
const fresh = (f) => ({ status: { failure_bits: 0, failsafe: false, sensor_state: ALLREADY, ...f }, statusState: "fresh" });

test("everything ready and clear is green, in the documented order", () => {
  const r = systemsStatus({ ...fresh({ calib_gyro_ok: true, calib_accel_ok: true, calib_mag_ok: true, ready_to_arm: true }), gps: { fix_type: 3, num_sats: 12 } });
  assert.deepEqual(r.map((x) => x.key), ["imu", "baro", "mag", "gnss", "attkf", "poskf", "b0", "b1", "b2", "b3", "b4", "b5", "failsafe", "calib.gyro", "calib.accel", "calib.mag", "calib.ready"]);
  assert.ok(r.every((x) => x.tone === "ok"), JSON.stringify(r.filter((x) => x.tone !== "ok")));
  assert.deepEqual(systemsSummary(r), { ok: 17, warn: 0, bad: 0, none: 0 });
});

test("sensor states: Init and Calib are yellow, Failure red, reason names the state and the raw value", () => {
  const r = bySys(systemsStatus(fresh({ sensor_state: ss(2, 0, 1, 3, 2, 2) })));
  assert.equal(r.imu.tone, "warn");
  assert.match(r.imu.reason, /Init \(sensor_state 0x/);
  assert.equal(r.baro.tone, "warn");
  assert.match(r.baro.reason, /Calib/);
  assert.equal(r.mag.tone, "bad");
  assert.match(r.mag.reason, /Failure/);
  assert.equal(r.attkf.tone, "ok");
});

test("GNSS: no fix is yellow, only a vehicle-reported failure is red", () => {
  assert.equal(gnssNoFix({ fix_type: 1, num_sats: 5 }), true);
  assert.equal(gnssNoFix({ fix_type: 3, num_sats: 0 }), true);
  assert.equal(gnssNoFix({ fix_type: 2, num_sats: 4 }), false);
  assert.equal(gnssNoFix({}), null);
  assert.equal(gnssNoFix(null), null);
  let g = bySys(systemsStatus({ ...fresh({}), gps: { fix_type: 0, num_sats: 0 } })).gnss;
  assert.equal(g.tone, "warn");
  assert.match(g.reason, /no fix/);
  g = bySys(systemsStatus({ ...fresh({ sensor_state: ss(0, 2, 2, 2, 2, 2) }), gps: { fix_type: 0, num_sats: 0 } })).gnss;
  assert.equal(g.tone, "warn"); // Init and no fix
  g = bySys(systemsStatus({ ...fresh({ sensor_state: ss(3, 2, 2, 2, 2, 2) }), gps: { fix_type: 0, num_sats: 0 } })).gnss;
  assert.equal(g.tone, "bad");
  assert.match(g.reason, /GNSS failure/);
  g = bySys(systemsStatus({ ...fresh({}), gps: { fix_type: 3, num_sats: 9 } })).gnss;
  assert.equal(g.tone, "ok");
  assert.match(g.reason, /9 satellites/);
});

test("failure bits and failsafe: set is red with the last-set age, clear is green", () => {
  const r = bySys(systemsStatus({ ...fresh({ failure_bits: 0b001000, failsafe: true }), lastSet: { 3: 4 }, now: 10 }));
  assert.equal(r.b3.tone, "bad");
  assert.equal(r.b3.label, "position out of bounds");
  assert.match(r.b3.reason, /ACTIVE, last set .* ago/);
  assert.equal(r.b4.tone, "ok");
  assert.equal(r.failsafe.tone, "bad");
  assert.equal(r.b0.label, "sensor failure");
});

test("calibration rows come from calib_state, the reason from the log text", () => {
  const calib = { state: "problem", detail: { gyro: "ok", accel: "parameters out of range", mag: "ok" } };
  const r = bySys(systemsStatus({ ...fresh({ calib_gyro_ok: true, calib_accel_ok: false, calib_mag_ok: true, ready_to_arm: false }), calib }));
  assert.equal(r["calib.gyro"].tone, "ok");
  assert.equal(r["calib.accel"].tone, "bad");
  assert.match(r["calib.accel"].reason, /INVALID: parameters out of range/);
  assert.equal(r["calib.ready"].tone, "bad");
  // log fallback when the vehicle sends no calib_state
  const f = bySys(systemsStatus({ ...fresh({}), calib }));
  assert.equal(f["calib.accel"].tone, "bad");
  assert.equal(f["calib.mag"].tone, "ok");
  assert.equal(f["calib.ready"].tone, "");
});

test("missing or stale VehicleStatus is grey with a reason, never green", () => {
  for (const state of ["missing", "stale"]) {
    const r = systemsStatus({ ...fresh({}), statusState: state });
    assert.ok(r.filter((x) => x.group !== "calib").every((x) => x.tone === ""), state);
    assert.match(r[0].reason, state === "stale" ? /stale/ : /no VehicleStatus/);
  }
  assert.ok(systemsStatus().every((x) => x.tone === ""));
  // no fix is still visible without VehicleStatus
  assert.equal(bySys(systemsStatus({ gps: { fix_type: 0, num_sats: 0 } })).gnss.tone, "warn");
});

test("estimator tooltips carry the sigma values and the ground distance", () => {
  const r = bySys(systemsStatus({ ...fresh({}), estimator: { att_std: [0.01, 0.02, 0.03], vel_std: [0.1, 0.1, 0.2], pos_std: [1, 1, 2] }, attitude: { tilt_acc: 0.01, north_acc: 0.02 }, groundDistance: 1.5 }));
  assert.match(r.attkf.reason, /att σ N\/W\/U .* °; tilt acc/);
  assert.match(r.poskf.reason, /vel σ 0.10 \/ 0.10 \/ 0.20 m\/s; pos σ .* m; ground distance 1.50 m/);
});
