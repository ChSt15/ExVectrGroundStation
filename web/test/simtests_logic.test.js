import test from "node:test";
import assert from "node:assert/strict";
import {
  AT_CURRENT_ANGLE, clearCommand, degToRad, describeFaultSent, forcedFlaps, radToDeg, simTestState, slowCommand,
  stuckCommand, flapIndex,
} from "../app/simtests/simtests_logic.js";

const FL = ["TL", "TR", "BL", "BR"];
const cmds = { SIM_FLAP_SLOW: { arg_f: { min: 0.05, max: 1 } } };
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);

test("degrees to radians and back", () => {
  near(degToRad(42), 0.7330382858376184);
  near(degToRad(90), Math.PI / 2);
  near(radToDeg(degToRad(42)), 42);
});

test("arg_i is the flap index 0..3 (TL TR BL BR)", () => {
  assert.deepEqual(FL.map((f) => flapIndex(f)), [0, 1, 2, 3]);
  assert.throws(() => flapIndex("XX"));
});

test("force-flap request converts degrees, supports hold-current", () => {
  const c = stuckCommand({ flap: "TL", atCurrent: false, deg: "42" });
  assert.equal(c.action, "SIM_FLAP_STUCK");
  assert.equal(c.arg_i, 0);
  near(c.arg_f, 0.7330382858376184);
  assert.equal(stuckCommand({ flap: "BR", atCurrent: false, deg: "90" }).arg_i, 3);
  assert.equal(stuckCommand({ flap: "BR", atCurrent: true, deg: "junk" }).arg_f, AT_CURRENT_ANGLE);
  for (const bad of ["", "91", "-1", "abc", "NaN"]) assert.equal(stuckCommand({ flap: "TL", atCurrent: false, deg: bad }), null);
});

test("slow and clear requests", () => {
  assert.deepEqual(slowCommand(cmds, { flap: "BL", factor: "0.3" }), { action: "SIM_FLAP_SLOW", arg_i: 2, arg_f: 0.3 });
  for (const bad of ["0.04", "1.01", "", "x"]) assert.equal(slowCommand(cmds, { flap: "TL", factor: bad }), null);
  assert.deepEqual(clearCommand(), { action: "SIM_FAULT_CLEAR" });
});

test("forced? hint: commanded vs true angle > 2 deg for > 1 s, display only", () => {
  const tr = {};
  const fs = { angle: [0.73, 0.5, 0.5, 0.5], cmd: [1.2, 0.51, 0.5, 0.5] };
  assert.deepEqual(forcedFlaps(tr, fs, 10).forced, [false, false, false, false]); // just started
  assert.ok(Math.abs(forcedFlaps(tr, fs, 10.5).angle[0] - radToDeg(0.73)) < 1e-9);
  assert.deepEqual(forcedFlaps(tr, fs, 11.2).forced, [true, false, false, false]);
  assert.deepEqual(forcedFlaps(tr, { angle: [0.73, 0.5, 0.5, 0.5], cmd: [0.73, 0.5, 0.5, 0.5] }, 12).forced, [false, false, false, false]);
  assert.deepEqual(forcedFlaps({}, null, 1), { forced: [false, false, false, false], angle: [null, null, null, null] });
  assert.deepEqual(forcedFlaps({}, { angle: [0.1, 0.1, 0.1, 0.1] }, 1).forced, [false, false, false, false]); // no cmd
});

test("gating: every simulation test needs the vehicle's own simulation flag, live view, control host, route", () => {
  const ok = { apiState: "open", view: "live", canControl: true, route: "usb", simulation: true };
  assert.equal(simTestState(ok).enabled, true);
  for (const bad of [{ simulation: false }, { simulation: null }, { simulation: undefined }, { view: "replay" }, { canControl: false }, { route: null }, { apiState: "closed" }, { inputsOk: false }, { busy: true }]) {
    assert.equal(simTestState({ ...ok, ...bad }).enabled, false, JSON.stringify(bad));
  }
});

test("fault result text is the vehicle's ACK in plain words", () => {
  assert.match(describeFaultSent(null).text, /no fault request/);
  const reqs = { inflight: [], recent: [{ req_id: 3, action: "SIM_FAULT_CLEAR", label: "Release all faults", state: "acked", status_name: "DONE", detail: 0 }, { req_id: 2, action: "SIM_WIND_SPEED", state: "acked", status_name: "DONE", detail: 0 }] };
  const d = describeFaultSent(reqs);
  assert.match(d.text, /Release all faults: ACK DONE/);
  assert.equal(d.tone, "ok");
  const r = describeFaultSent({ recent: [{ req_id: 4, action: "SIM_FLAP_STUCK", state: "acked", status_name: "REJECTED_STATE", detail: 5 }] });
  assert.match(r.text, /not in simulation mode/);
  assert.equal(r.tone, "bad");
});
