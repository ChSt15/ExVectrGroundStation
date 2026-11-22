import test from "node:test";
import assert from "node:assert/strict";
import { calibAnyBad, calibRows, readyRow } from "../app/status/calib_rows.js";
import { LogWatcher } from "../app/callouts/logwatch.js";

const WARN = (why) => `[node 1 WARNING] WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): ${why}`;
const rig = () => {
  let t = 100;
  const w = new LogWatcher({ clock: () => t });
  return { w, at: (s) => (t = 100 + s) };
};
const by = (rows) => Object.fromEntries(rows.map((r) => [r.key, r]));

test("no information: three grey rows", () => {
  const r = rig();
  const rows = calibRows(r.w.calibState());
  assert.deepEqual(rows.map((x) => x.label), ["Gyro calibration", "Accel calibration", "Mag calibration"]);
  assert.ok(rows.every((x) => x.tone === "" && x.text === "—"));
  assert.equal(calibAnyBad(rows), false);
});

test("real warning: failing sensor red with the vehicle's reason, others green", () => {
  const cases = [
    ["gyro: last run failed, accel: ok, mag: ok", "gyro", "last run failed"],
    ["gyro: ok, accel: parameters out of range, mag: ok", "accel", "parameters out of range"],
    ["gyro: ok, accel: still-window check failed, mag: ok", "accel", "still-window check failed"],
    ["gyro: ok, accel: ok, mag: default (never calibrated)", "mag", "default (never calibrated)"],
  ];
  for (const [why, key, note] of cases) {
    const r = rig();
    r.w.feed(WARN(why), { notify: false });
    const rows = by(calibRows(r.w.calibState()));
    assert.equal(rows[key].tone, "bad", why);
    assert.equal(rows[key].note, note);
    for (const k of Object.keys(rows)) if (k !== key) assert.equal(rows[k].tone, "ok", why);
    assert.equal(calibAnyBad(Object.values(rows)), true);
  }
});

test("cleared by 'all three calibrations checked' and by 25 s without repeat: green", () => {
  const r = rig();
  r.w.feed(WARN("gyro: ok, accel: ok, mag: parameters out of range"), { notify: false });
  r.w.feed("[node 1 INFO] Calibration: all three calibrations checked, countdown continues", { notify: false });
  assert.ok(calibRows(r.w.calibState()).every((x) => x.tone === "ok"));
  const q = rig();
  q.w.feed(WARN("gyro: last run failed, accel: ok, mag: ok"), { notify: false });
  q.at(30);
  q.w.tick();
  assert.ok(calibRows(q.w.calibState()).every((x) => x.tone === "ok"));
});

test("reset returns to unknown", () => {
  const r = rig();
  r.w.feed(WARN("gyro: last run failed, accel: ok, mag: ok"), { notify: false });
  r.w.reset();
  assert.ok(calibRows(r.w.calibState()).every((x) => x.tone === ""));
});

// ---- structured VehicleStatus.calib_state (primary) with the log as reason text and fallback

const SV = (o) => ({ calib_gyro_ok: true, calib_accel_ok: true, calib_mag_ok: true, calib_session: false, ready_to_arm: true, ...o });

test("structured state is primary: green and red come from the booleans", () => {
  const r = rig();
  const rows = by(calibRows(r.w.calibState(), SV({ calib_accel_ok: false })));
  assert.equal(rows.gyro.tone, "ok");
  assert.equal(rows.mag.tone, "ok");
  assert.equal(rows.accel.tone, "bad");
  assert.equal(rows.accel.text, "INVALID");
  assert.equal(rows.accel.note, ""); // no log text: no reason
  assert.equal(calibAnyBad(Object.values(rows)), true);
});

test("structured ok wins over a stale log problem; the log gives the reason for a structured bad", () => {
  const r = rig();
  r.w.feed(WARN("gyro: last run failed, accel: ok, mag: ok"), { notify: false });
  r.at(1);
  assert.ok(calibRows(r.w.calibState(), SV({})).every((x) => x.tone === "ok"));
  const rows = by(calibRows(r.w.calibState(), SV({ calib_gyro_ok: false })));
  assert.equal(rows.gyro.tone, "bad");
  assert.equal(rows.gyro.note, "last run failed");
});

test("fallback to the log text when there is no calib_state (null fields or no status)", () => {
  const r = rig();
  r.w.feed(WARN("gyro: ok, accel: ok, mag: default (never calibrated)"), { notify: false });
  r.at(1);
  const nulls = { calib_gyro_ok: null, calib_accel_ok: null, calib_mag_ok: null, ready_to_arm: null };
  for (const st of [null, undefined, nulls, {}]) {
    const rows = by(calibRows(r.w.calibState(), st));
    assert.equal(rows.mag.tone, "bad");
    assert.equal(rows.gyro.tone, "ok");
  }
});

test("a partly structured status uses the structured rows only where it has a boolean", () => {
  const r = rig();
  const rows = by(calibRows(r.w.calibState(), { calib_gyro_ok: true, calib_accel_ok: null, calib_mag_ok: false }));
  assert.equal(rows.gyro.tone, "ok");
  assert.equal(rows.accel.tone, "");
  assert.equal(rows.mag.tone, "bad");
});

test("ready to arm row: green, red, and grey when unknown", () => {
  assert.deepEqual([readyRow(SV({})).tone, readyRow(SV({})).text], ["ok", "ready"]);
  assert.deepEqual([readyRow(SV({ ready_to_arm: false })).tone, readyRow(SV({ ready_to_arm: false })).text], ["bad", "NOT ready"]);
  for (const st of [null, {}, { ready_to_arm: null }]) assert.deepEqual([readyRow(st).tone, readyRow(st).text], ["", "—"]);
});
