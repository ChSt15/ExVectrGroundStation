import test from "node:test";
import assert from "node:assert/strict";
import { CALIBRATED_PLACEHOLDER, QUANTITIES, Series, magnitude, sparkValue, vehicleUsbConnected } from "../app/sensors/rawsensors_logic.js";

test("magnitude of a 3-vector, undefined for anything else", () => {
  assert.equal(magnitude([3, 4, 0]), 5);
  assert.ok(Math.abs(magnitude([0, 0, 9.81]) - 9.81) < 1e-12);
  assert.equal(magnitude([1, 2]), undefined);
  assert.equal(magnitude([1, null, 3]), undefined);
  assert.equal(magnitude(undefined), undefined);
});

test("quantities: units and the µT display scale of the SI tesla values", () => {
  const m = QUANTITIES.find((q) => q.key === "mag");
  assert.equal(m.unit, "µT");
  assert.equal(m.scale, 1e6);
  assert.deepEqual(QUANTITIES.filter((q) => q.magnitude).map((q) => q.key), ["accel", "mag"]);
  assert.deepEqual(QUANTITIES.map((q) => q.key), ["gyro", "accel", "mag", "pressure", "temperature"]);
});

test("the calibrated column is a placeholder until a CALIBRATION packet exists", () => {
  assert.equal(CALIBRATED_PLACEHOLDER, "waiting for CALIBRATION packet");
});

test("sparkValue: magnitude for vectors, the number for scalars", () => {
  assert.equal(sparkValue({ vec: true }, [0, 3, 4]), 5);
  assert.equal(sparkValue({ vec: false }, 101325), 101325);
  assert.equal(sparkValue({ vec: false }, NaN), undefined);
});

test("series keeps a time window, resets when time goes backwards, and scales the polyline", () => {
  const s = new Series(10);
  for (let t = 0; t <= 30; t += 1) s.add(t, t % 2);
  assert.ok(s.pts[0][0] >= 20);
  const pts = s.path(100, 20).split(" ");
  assert.equal(pts.length, s.pts.length);
  assert.ok(pts.every((p) => { const [x, y] = p.split(",").map(Number); return x >= 0 && x <= 100 && y >= 0 && y <= 20; }));
  s.add(5, 1); // seek back
  assert.equal(s.pts.length, 1);
  assert.equal(s.path(100, 20), "");
  s.add(6, NaN);
  assert.equal(s.pts.length, 1);
});

test("a constant series draws a flat line, not NaN", () => {
  const s = new Series(10);
  s.add(0, 5);
  s.add(1, 5);
  assert.ok(!s.path(100, 20).includes("NaN"));
});

test("vehicleUsbConnected only for a connected vehicle_usb source", () => {
  assert.equal(vehicleUsbConnected(null), false);
  assert.equal(vehicleUsbConnected({ sources: [{ kind: "link_module", state: "connected" }] }), false);
  assert.equal(vehicleUsbConnected({ sources: [{ kind: "vehicle_usb", state: "searching" }] }), false);
  assert.equal(vehicleUsbConnected({ sources: [{ kind: "vehicle_usb", state: "connected" }] }), true);
});

// ---- calibrated column: the formulas of flight-software docs section 8a, checked with the Packets test vectors
// (imu_raw and calibration cases of lib/ExVectrPackets/test/vectors/packets.json, copied to test/data by a Python
// test that fails when they drift apart).
import { SOURCE_BITS, calibrate, computeCalibrated, decodeSource, matVec } from "../app/sensors/rawsensors_logic.js";

async function loadVectors() {
  const url = new URL("./data/calibration_vectors.json", import.meta.url);
  if (typeof process !== "undefined" && process.versions && process.versions.node) {
    const fs = await import("node:fs");
    return JSON.parse(fs.readFileSync(url, "utf8"));
  }
  return (await fetch(url)).json();
}
const V = await loadVectors();
const close = (a, b, tol = 1e-9) => assert.ok(Math.abs(a - b) <= tol, `${a} vs ${b}`);
const closeVec = (a, b, tol = 1e-9) => {
  assert.equal(a.length, b.length);
  a.forEach((x, i) => close(x, b[i], tol));
};

test("matVec is row-major: M[r][c] = matrix[3r + c]", () => {
  assert.deepEqual(matVec([1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 0, 0]), [1, 4, 7]);
  assert.deepEqual(matVec([1, 2, 3, 4, 5, 6, 7, 8, 9], [0, 1, 0]), [2, 5, 8]);
  assert.deepEqual(matVec([1, 0, 0, 0, 1, 0, 0, 0, 1], [3, 4, 5]), [3, 4, 5]);
  assert.equal(matVec([1, 2], [1, 2, 3]), undefined);
  assert.equal(matVec([1, 0, 0, 0, 1, 0, 0, 0, 1], [1, null, 3]), undefined);
});

test("vector: calibrated values of the shared test vectors equal M * (raw - offset)", () => {
  const imu = V.imu_raw;
  const cal = V.calibration;
  const r = computeCalibrated(imu, cal);
  // gyro: raw [0.5, 0.25, -0.125] - [0.015625, 0.03125, -0.0078125], M = diag(1, -1, 0.5)
  closeVec(r.gyro, [0.484375, -0.21875, -0.05859375]);
  // accel: raw [0, 0, 9.75] - [0.25, -0.5, 1.5] = [-0.25, 0.5, 8.25], M = [[0,1,0],[1,0,0],[0,0,-1]]
  closeVec(r.accel, [0.5, -0.25, -8.25]);
  // mag: 2 * (raw - offset), raw [2, -4, 8], offset [1.5e-5, -2.5e-6, 4e-6]
  closeVec(r.mag, [2 * (2 - 1.5e-5), 2 * (-4 + 2.5e-6), 2 * (8 - 4e-6)], 1e-6);
  // the vector's gyro_bias_est has a NaN (null) component: no estimate is guessed
  assert.equal(r.omega_est, undefined);
  const r2 = computeCalibrated(imu, { ...cal, gyro_bias_est: [0.001, 0, -0.002] });
  closeVec(r2.omega_est, [0.484375 - 0.001, -0.21875, -0.05859375 + 0.002]);
  close(magnitude(r.accel), Math.hypot(0.5, 0.25, 8.25));
});

test("a still accelerometer with an identity calibration gives about 9.81 m/s^2", () => {
  const I = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const r = computeCalibrated({ gyro: [0, 0, 0], accel: [0, 0.1, 9.81], mag: [0, 0, 0] }, { raw_frame: 0, gyro_offset: [0, 0, 0], gyro_matrix: I, accel_offset: [0, 0.1, 0], accel_matrix: I, mag_offset: [0, 0, 0], mag_matrix: I, gyro_bias_est: [0, 0, 0] });
  close(magnitude(r.accel), 9.81);
  assert.deepEqual(calibrate([1, 2, 3], [1, 2, 3], I), [0, 0, 0]);
});

test("missing input or another raw frame gives no calibrated value", () => {
  assert.deepEqual(computeCalibrated(undefined, V.calibration), { gyro: undefined, accel: undefined, mag: undefined, omega_est: undefined });
  assert.deepEqual(computeCalibrated(V.imu_raw, undefined), { gyro: undefined, accel: undefined, mag: undefined, omega_est: undefined });
  assert.equal(computeCalibrated(V.imu_raw, { ...V.calibration, raw_frame: 1 }).gyro, undefined);
  assert.equal(calibrate([1, 2, null], [0, 0, 0], [1, 0, 0, 0, 1, 0, 0, 0, 1]), undefined);
});

test("source flags decode to readable names", () => {
  assert.equal(decodeSource(0), "identity defaults");
  assert.equal(decodeSource(0b11), "calib EEPROM, transform EEPROM");
  assert.equal(decodeSource(0x07), "calib EEPROM, transform EEPROM, offset override (hard-coded)");
  assert.equal(decodeSource(1 << 3), "RAM only (not saved)");
  assert.equal(decodeSource(1 << 4), "memory cleared (defaults)");
  assert.equal(decodeSource(25), "calib EEPROM, RAM only (not saved), memory cleared (defaults)"); // the vector's mag_source
  assert.equal(SOURCE_BITS.length, 5);
  assert.equal(decodeSource(undefined), "n/s");
});
