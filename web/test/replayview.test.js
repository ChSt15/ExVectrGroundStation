import test from "node:test";
import assert from "node:assert/strict";
import { clampToLog, logKey, logRange, nearBar, replayWindow } from "../app/plot/replayview.js";

const range = [100, 700];

test("log range and key come from the replay status", () => {
  assert.equal(logRange(null), null);
  assert.equal(logRange({ state: "loading", t_start: 1, t_end: 2 }), null);
  assert.deepEqual(logRange({ state: "paused", t_start: 100, t_end: 700 }), range);
  assert.equal(logKey({ state: "closed" }), null);
  assert.equal(logKey({ state: "paused", name: "a", t_start: 1, t_end: 2 }), "a|1|2");
});

test("replay window: whole recording by default, presets centred on the position, frozen after a zoom", () => {
  assert.deepEqual(replayWindow({ range, pos: 300, windowS: Infinity, frozen: null }), range);
  assert.deepEqual(replayWindow({ range, pos: 300, windowS: 10_000, frozen: null }), range);
  assert.deepEqual(replayWindow({ range, pos: 300, windowS: 30, frozen: null }), [285, 315]);
  assert.deepEqual(replayWindow({ range, pos: 101, windowS: 30, frozen: null }), [100, 130]);
  assert.deepEqual(replayWindow({ range, pos: 699, windowS: 30, frozen: null }), [670, 700]);
  assert.deepEqual(replayWindow({ range, pos: 300, windowS: Infinity, frozen: [200, 250] }), [200, 250]);
  assert.deepEqual(replayWindow({ range, pos: 300, windowS: 30, frozen: [690, 720] }), [670, 700]);
});

test("seek position clamps into the log, the bar is grabbed near its pixel", () => {
  assert.equal(clampToLog(50, range), 100);
  assert.equal(clampToLog(800, range), 700);
  assert.equal(clampToLog(400, range), 400);
  assert.equal(nearBar(103, 100), true);
  assert.equal(nearBar(110, 100), false);
  assert.equal(nearBar(100, null), false);
});
