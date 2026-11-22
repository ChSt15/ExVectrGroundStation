import test from "node:test";
import assert from "node:assert/strict";
import { CHIP_STALE_S, DEDUPE_S, LogWatcher } from "../app/callouts/logwatch.js";

const WHY = "gyro: ok, accel: parameters out of range, mag: ok";
const REPEAT = `[node 1 WARNING] WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): ${WHY}`;

function rig() {
  let t = 100;
  const out = [];
  const w = new LogWatcher({ clock: () => t, emit: (c, repeat) => out.push([c.title, repeat]) });
  return { w, out, at: (s) => (t = 100 + s) };
}

test("identical text within 10 s is flagged as a repeat, after 10 s it is a new toast", () => {
  const r = rig();
  const line = "[node 1 WARNING] Radio retry";
  r.w.feed(line);
  r.at(3);
  r.w.feed(line);
  r.at(DEDUPE_S - 0.1);
  r.w.feed(line);
  r.at(DEDUPE_S + 0.5);
  r.w.feed(line);
  assert.deepEqual(r.out.map((x) => x[1]), [false, true, true, false]);
  r.w.feed("[node 1 WARNING] Other");
  assert.equal(r.out.at(-1)[1], false);
});

test("calibration chip: set from the repeated warning, toast only when it appears or changes", () => {
  const r = rig();
  assert.equal(r.w.chip(), null);
  r.w.feed(REPEAT);
  assert.equal(r.w.chip().text, "Calibration invalid: accel");
  r.at(10);
  r.w.feed(REPEAT);
  r.at(20);
  r.w.feed(REPEAT);
  assert.equal(r.out.length, 1); // one toast, the chip carries the rest
  r.at(30);
  r.w.feed(REPEAT.replace("mag: ok", "mag: last run failed"));
  assert.equal(r.w.chip().text, "Calibration invalid: accel, mag");
  assert.equal(r.out.length, 2);
});

test("chip clears 25 s after the last repeat, stays while repeats continue", () => {
  const r = rig();
  r.w.feed(REPEAT);
  r.at(10);
  r.w.feed(REPEAT);
  r.at(10 + CHIP_STALE_S - 1);
  r.w.tick();
  assert.ok(r.w.chip());
  r.at(10 + CHIP_STALE_S + 1);
  r.w.tick();
  assert.equal(r.w.chip(), null);
});

test("chip clears when the vehicle reports the calibrations ok", () => {
  const r = rig();
  r.w.feed(REPEAT);
  r.w.feed("[node 1 INFO] Calibration: all three calibrations checked at the end of the gyro run, countdown continues");
  assert.equal(r.w.chip(), null);
});

test("a refusal with the reason also sets the chip (and still toasts every time)", () => {
  const r = rig();
  r.w.feed(`[node 1 WARNING] Arm refused: calibration invalid (${WHY})`);
  assert.equal(r.w.chip().text, "Calibration invalid: accel");
  assert.equal(r.out.length, 1);
});

test("unread count: WARNING/ERROR lines of any node since markRead, INFO does not count", () => {
  const r = rig();
  r.w.feed("[node 1 INFO] Calibration: Accel: 2 poses captured (moving)");
  r.w.feed("[node 1 WARNING] a");
  r.w.feed("[node 2 ERROR] b");
  r.w.feed("[node 1 INFO] Calibration: Accel: pose 1 captured, move to the next one"); // event, but not a problem level
  assert.equal(r.w.unread(), 2);
  r.w.markRead();
  assert.equal(r.w.unread(), 0);
  r.w.feed("[node 1 WARNING] c");
  assert.equal(r.w.unread(), 1);
});

test("reset forgets chip, dedupe memory and unread", () => {
  const r = rig();
  r.w.feed(REPEAT);
  r.w.reset();
  assert.equal(r.w.chip(), null);
  assert.equal(r.w.unread(), 0);
  r.w.feed("[node 1 WARNING] x");
  r.w.feed("[node 1 WARNING] x");
  r.w.reset();
  r.w.feed("[node 1 WARNING] x");
  assert.equal(r.out.at(-1)[1], false);
});
