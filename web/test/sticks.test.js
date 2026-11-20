import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_STICKS, normCentered, normThrottle, stickConfig, stickState, throttleAxis } from "../app/radio/sticks.js";

test("pulse width to normalised stick values", () => {
  assert.equal(normCentered(1500), 0);
  assert.equal(normCentered(1000), -1);
  assert.equal(normCentered(2000), 1);
  assert.equal(normCentered(1750), 0.5);
  assert.equal(normCentered(900), -1); // clamped
  assert.equal(normCentered(2100), 1);
  assert.equal(normCentered(undefined), undefined);
  assert.equal(normCentered(NaN), undefined);
  assert.equal(normThrottle(1000), 0);
  assert.equal(normThrottle(2000), 1);
  assert.equal(normThrottle(1250), 0.25);
  assert.equal(normThrottle(950), 0);
  assert.equal(throttleAxis(0), -1);
  assert.equal(throttleAxis(1), 1);
});

test("stick config from the radio config, defaults for missing keys", () => {
  assert.deepEqual(stickConfig(null), DEFAULT_STICKS);
  assert.deepEqual(stickConfig({ sticks: { mode: 1, yaw: 6, bogus: 3, roll: "x" } }), { ...DEFAULT_STICKS, mode: 1, yaw: 6 });
});

test("mode 2: left = yaw / throttle, right = roll / pitch; safe defaults are centred with throttle low", () => {
  const s = stickState(DEFAULT_STICKS, [1500, 1500, 1000, 1500, 1000, 1000]);
  assert.deepEqual([s.left.xLabel, s.left.yLabel, s.right.xLabel, s.right.yLabel], ["yaw", "throttle", "roll", "pitch"]);
  assert.deepEqual([s.left.x, s.left.y, s.right.x, s.right.y], [0, -1, 0, 0]);
  assert.equal(s.throttle, 0);
  assert.equal(s.left.yUs, 1000);
  assert.equal(s.left.yCh, 3);
  const moved = stickState(DEFAULT_STICKS, [2000, 1250, 1500, 1000]);
  assert.deepEqual([moved.right.x, moved.right.y, moved.left.x, moved.left.y], [1, -0.5, -1, 0]);
  assert.equal(moved.values.throttle, 0.5);
});

test("mode 1 and a remapped config; missing channels give undefined, never a guess", () => {
  const s = stickState({ ...DEFAULT_STICKS, mode: 1 }, [1500, 2000, 1000, 1500]);
  assert.deepEqual([s.left.xLabel, s.left.yLabel, s.right.xLabel, s.right.yLabel], ["yaw", "pitch", "roll", "throttle"]);
  assert.equal(s.left.y, 1);
  assert.equal(s.right.y, -1);
  const remap = stickState({ mode: 2, roll: 4, pitch: 3, throttle: 2, yaw: 1 }, [1500, 2000, 1500, 1500]);
  assert.equal(remap.throttle, 1);
  const none = stickState(DEFAULT_STICKS, []);
  assert.equal(none.left.x, undefined);
  assert.equal(none.throttle, undefined);
});
