import test from "node:test";
import assert from "node:assert/strict";
import { calibAnyBad, calibRows } from "../app/status/calib_rows.js";
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
