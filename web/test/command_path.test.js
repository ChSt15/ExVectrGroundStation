import test from "node:test";
import assert from "node:assert/strict";
import { commandPathRows, COUNTDOWN_NOTE } from "../app/status/command_path.js";

const rowsOf = (f) => Object.fromEntries(commandPathRows({ f }).map((r) => [r.key, r]));

test("command path rows: nothing without VehicleStatus", () => {
  assert.deepEqual(commandPathRows(null), []);
});

test("command path rows: vehicle-reported mode, motors armed, flags, refusal, profile and countdown note", () => {
  const r = rowsOf({ simulation: true, armed: true, ctrl_flags: (1 << 0) | (1 << 2) | (1 << 4) | (1 << 5) | (1 << 6), go_refusal: 3, mission_profile: 2 });
  assert.equal(r.mode.text, "SIMULATION");
  assert.equal(r.interlock.text, "MOTORS ARMED");
  assert.equal(r.flags.text, "Toggle SD off, then on (motors armed), FAILSAFE latched, SC mismatch, SE hold zeroing, SE refused");
  assert.equal(r.flags.tone, "bad");
  assert.equal(r.go_refusal.text, "estimator zeroing window or no valid zero");
  assert.equal(r.mission_profile.text, "2");
  assert.equal(r.countdown.text, COUNTDOWN_NOTE);
  const q = rowsOf({ simulation: false, armed: false, ctrl_flags: 0, go_refusal: 0, mission_profile: 255 });
  assert.equal(q.mode.text, "REAL SENSORS");
  assert.equal(q.flags.text, "nothing pending");
  assert.equal(q.go_refusal, undefined);
  assert.equal(q.mission_profile, undefined);
});

test("command path rows: MULTI_MASTER and a GS failsafe request are shown (bad)", () => {
  const r = rowsOf({ simulation: true, armed: false, ctrl_flags: (1 << 11) | (1 << 12), go_refusal: 0, mission_profile: 255 });
  assert.equal(r.flags.text, "MULTI_MASTER, GS failsafe request");
  assert.equal(r.flags.tone, "bad");
});
