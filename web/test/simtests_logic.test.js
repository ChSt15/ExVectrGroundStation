import test from "node:test";
import assert from "node:assert/strict";
import {
  AT_CURRENT_ANGLE, clearCommand, degToRad, describeFaultSent, forcedFlaps, radToDeg, simTestState, slowCommand,
  stuckCommand, stuckSubsystem,
} from "../app/telecommand/simtests_logic.js";

const FL = ["TL", "TR", "BL", "BR"];
const cmds = [
  ...FL.map((f, i) => ({ name: `Stuck${f}`, type: "SimFlapStuck", subsystem: i })),
  ...FL.map((f, i) => ({ name: `Slow${f}`, type: "SimFlapSlow", subsystem: i, param_float_min: 0.05, param_float_max: 1 })),
  { name: "Clear", type: "SimFaultClear", subsystem: 0 },
];
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);

test("degrees to radians and back", () => {
  near(degToRad(42), 0.7330382858376184);
  near(degToRad(90), Math.PI / 2);
  near(radToDeg(degToRad(42)), 42);
});

test("subsystem byte: the flap index only, no arm bit", () => {
  assert.deepEqual(FL.map((f) => stuckSubsystem(f)), [0, 1, 2, 3]);
  assert.throws(() => stuckSubsystem("XX"));
});

test("force-flap command picks the entry, converts degrees, supports hold-current", () => {
  const c = stuckCommand(cmds, { flap: "TL", atCurrent: false, deg: "42" });
  assert.equal(c.name, "StuckTL");
  near(c.value, 0.7330382858376184);
  assert.equal(stuckCommand(cmds, { flap: "BR", atCurrent: false, deg: "90" }).name, "StuckBR");
  assert.equal(stuckCommand(cmds, { flap: "BR", atCurrent: true, deg: "junk" }).value, AT_CURRENT_ANGLE);
  for (const bad of ["", "91", "-1", "abc", "NaN"]) assert.equal(stuckCommand(cmds, { flap: "TL", atCurrent: false, deg: bad }), null);
  assert.equal(stuckCommand([], { flap: "TL", atCurrent: false, deg: "42" }), null);
});

test("slow and clear commands", () => {
  assert.deepEqual(slowCommand(cmds, { flap: "BL", factor: "0.3" }), { name: "SlowBL", value: 0.3 });
  for (const bad of ["0.04", "1.01", "", "x"]) assert.equal(slowCommand(cmds, { flap: "TL", factor: bad }), null);
  assert.equal(clearCommand(cmds).name, "Clear");
  assert.equal(clearCommand([]), null);
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

test("sent text comes from the core record only and says no ACK exists", () => {
  assert.match(describeFaultSent(null, 10).text, /no fault command/);
  const push = { recent: [{ n: 3, name: "Clear", label: "Clear all", type: "SimFaultClear", t: 8, source: "usb", param_int: 87, param_float: 0 }, { n: 2, name: "Arm", type: "Arm", t: 9, source: "usb", param_int: 1 }] };
  const d = describeFaultSent(push, 10);
  assert.match(d.text, /#3 sent .* ago once via usb.*no ACK exists/);
});
