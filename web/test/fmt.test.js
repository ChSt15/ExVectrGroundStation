import test from "node:test";
import assert from "node:assert/strict";
import { describe, fmtNum, fmtAge, fmtClock, fmtMissionTime, fmtBytes, quatToRpy, tiltFromVertical, staleAfter, worstState } from "../app/fmt.js";

const NOW = 100;
const entry = (type, f, t = NOW, extra = {}) => ({ type, src: "usb", t, f, iv: 0.2, stale_after: 1.0, ...extra });
const lookup = (entries) => (type, src) => entries.find((e) => e.type === type && (!src || e.src === src));

test("fmtNum", () => {
  assert.equal(fmtNum(1.2345, 2), "1.23");
  assert.equal(fmtNum(-0.001, 2), "0.00");
  assert.equal(fmtNum(NaN), "NaN");
  assert.equal(fmtNum(Infinity), "inf");
  assert.equal(fmtNum(-Infinity), "-inf");
  assert.equal(fmtNum(null), "—");
});

test("fmtAge, fmtClock, fmtMissionTime, fmtBytes", () => {
  assert.equal(fmtAge(0.34), "0.3 s");
  assert.equal(fmtAge(12.4), "12 s");
  assert.equal(fmtAge(125), "2 min 5 s");
  assert.equal(fmtAge(7300), "2 h 1 min");
  assert.equal(fmtClock(75), "01:15");
  assert.equal(fmtClock(3725), "1:02:05");
  assert.equal(fmtClock(null), "--:--");
  assert.equal(fmtMissionTime(-3), "T−3.00 s");
  assert.equal(fmtMissionTime(4.25), "T+4.25 s");
  assert.equal(fmtBytes(1500), "1.5 kB");
  assert.equal(fmtBytes(12), "12 B");
});

test("fresh value with source and unit", () => {
  const e = [entry("Position", { pos: [1, 2.5, -3] })];
  const d = describe({ type: "Position", field: "pos", kind: "vec", unit: "m" }, lookup(e), NOW + 0.3);
  assert.equal(d.state, "fresh");
  assert.equal(d.text, "1.00  2.50  -3.00");
  assert.equal(d.unit, "m");
  assert.equal(d.src, "usb");
});

test("stale value keeps its value and reports the age", () => {
  const e = [entry("Attitude", { tilt_acc: 0.5 }, 90)];
  const d = describe({ type: "Attitude", field: "tilt_acc", digits: 1 }, lookup(e), NOW);
  assert.equal(d.state, "stale");
  assert.equal(d.text, "0.5");
  assert.equal(d.age, 10);
});

test("missing: nothing received for the type; not supplied: packet without the field", () => {
  const e = [entry("Position", { pos: [0, 0, 0] })];
  assert.equal(describe({ type: "Attitude", field: "q" }, lookup(e), NOW).state, "missing");
  const d = describe({ type: "Position", field: "h_acc" }, lookup(e), NOW);
  assert.equal(d.state, "not supplied");
  assert.equal(d.text, "n/s");
});

test("array index and non-finite markers", () => {
  const e = [entry("Position", { pos: [1, null, 3] }, NOW, { nf: { pos: [null, "nan", null] } })];
  assert.equal(describe({ type: "Position", field: "pos", index: 2 }, lookup(e), NOW).text, "3.00");
  assert.equal(describe({ type: "Position", field: "pos", index: 1 }, lookup(e), NOW).text, "NaN");
  assert.equal(describe({ type: "Position", field: "pos", kind: "vec" }, lookup(e), NOW).text, "1.00  NaN  3.00");
  const e2 = [entry("Gps", { alt: null }, NOW, { nf: { alt: "inf" } })];
  assert.equal(describe({ type: "Gps", field: "alt" }, lookup(e2), NOW).text, "inf");
});

test("enum and bool kinds", () => {
  const e = [entry("VehicleState", { mode: "Running", armed: true })];
  assert.equal(describe({ type: "VehicleState", field: "mode", kind: "enum" }, lookup(e), NOW).text, "Running");
  assert.equal(describe({ type: "VehicleState", field: "armed", kind: "bool", labels: ["disarmed", "ARMED"] }, lookup(e), NOW).text, "ARMED");
});

test("derived values over several types: worst age and joined sources decide", () => {
  const e = [entry("MissionState", { sp_pos: [1, 1, 1] }, NOW), entry("Position", { pos: [0, 0, 0] }, 90, { src: "link" })];
  const d = describe({ types: ["MissionState", "Position"], kind: "num", derive: ([m, p]) => m.f.sp_pos[0] - p.f.pos[0] }, lookup(e), NOW);
  assert.equal(d.state, "stale");
  assert.equal(d.src, "usb+link");
  assert.equal(d.text, "1.00");
  const none = describe({ types: ["MissionState", "Attitude"], derive: () => 1 }, lookup(e), NOW);
  assert.equal(none.state, "missing");
});

test("derive returning undefined is not supplied", () => {
  const e = [entry("Gps", { alt: 1 })];
  assert.equal(describe({ type: "Gps", derive: () => undefined }, lookup(e), NOW).state, "not supplied");
});

test("staleAfter falls back to the schema rate, then to a default", () => {
  assert.equal(staleAfter({ stale_after: 0.7 }, undefined), 0.7);
  assert.equal(staleAfter({}, { nominal_rate_hz: 10 }), 0.5);
  assert.equal(staleAfter({}, { nominal_rate_hz: 1 }), 3);
  assert.equal(staleAfter({}, undefined), 2);
});

test("worstState order", () => {
  assert.equal(worstState(["fresh", "stale"]), "stale");
  assert.equal(worstState(["fresh", "missing", "stale"]), "missing");
  assert.equal(worstState(["fresh"]), "fresh");
});

test("quaternion to roll/pitch/yaw (ZYX) and tilt", () => {
  const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);
  const h = Math.SQRT1_2;
  assert.deepEqual(quatToRpy([1, 0, 0, 0]), [0, 0, 0]);
  const yaw90 = quatToRpy([h, 0, 0, h]);
  near(yaw90[2], Math.PI / 2);
  const roll90 = quatToRpy([h, h, 0, 0]);
  near(roll90[0], Math.PI / 2);
  const pitch90 = quatToRpy([h, 0, h, 0]);
  near(pitch90[1], Math.PI / 2);
  near(tiltFromVertical([1, 0, 0, 0]), 0);
  near(tiltFromVertical([h, h, 0, 0]), Math.PI / 2); // rolled 90 deg: body Z horizontal
  near(tiltFromVertical([0, 1, 0, 0]), Math.PI); // upside down
  near(tiltFromVertical([h, 0, 0, h]), 0); // yaw does not tilt
  assert.equal(quatToRpy([1, 2]), undefined);
  assert.equal(tiltFromVertical(undefined), undefined);
});
