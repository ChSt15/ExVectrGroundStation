import test from "node:test";
import assert from "node:assert/strict";
import {
  AT_CURRENT_ANGLE, LAST_FLIGHT_PRESET, clearCommand, degToRad, describeFaultSent, radToDeg, simTestState, slowCommand,
  stuckCommand, stuckSubsystem,
} from "../app/telecommand/simtests_logic.js";

const FL = ["TL", "TR", "BL", "BR"];
const cmds = [
  ...[false, true].flatMap((arm) => FL.map((f, i) => ({ name: `Stuck${f}${arm ? "D" : "N"}`, type: "SimFlapStuck", subsystem: i | (arm ? 0x10 : 0) }))),
  ...FL.map((f, i) => ({ name: `Slow${f}`, type: "SimFlapSlow", subsystem: i, param_float_min: 0.05, param_float_max: 1 })),
  { name: "Clear", type: "SimFaultClear", subsystem: 0 },
];
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);

test("degrees to radians and back", () => {
  near(degToRad(42), 0.7330382858376184);
  near(degToRad(90), Math.PI / 2);
  near(radToDeg(degToRad(42)), 42);
});

test("subsystem byte: flap index in the low 2 bits, 0x10 = armed for the descent", () => {
  assert.deepEqual(FL.map((f) => stuckSubsystem(f, false)), [0, 1, 2, 3]);
  assert.deepEqual(FL.map((f) => stuckSubsystem(f, true)), [0x10, 0x11, 0x12, 0x13]);
  assert.throws(() => stuckSubsystem("XX", false));
});

test("stuck command picks the entry, converts degrees, supports the current-angle value", () => {
  const c = stuckCommand(cmds, { flap: "TL", armed: true, atCurrent: false, deg: "42" });
  assert.equal(c.name, "StuckTLD");
  near(c.value, degToRad(42));
  assert.equal(stuckCommand(cmds, { flap: "BR", armed: false, atCurrent: false, deg: "90" }).name, "StuckBRN");
  assert.equal(stuckCommand(cmds, { flap: "TL", armed: false, atCurrent: true, deg: "junk" }).value, AT_CURRENT_ANGLE);
  for (const bad of ["", "91", "-1", "abc", "NaN"]) assert.equal(stuckCommand(cmds, { flap: "TL", armed: false, atCurrent: false, deg: bad }), null);
  assert.equal(stuckCommand([], { flap: "TL", armed: false, atCurrent: false, deg: "42" }), null);
});

test("slow and clear commands", () => {
  assert.deepEqual(slowCommand(cmds, { flap: "BL", factor: "0.3" }), { name: "SlowBL", value: 0.3 });
  for (const bad of ["0.04", "1.01", "", "x"]) assert.equal(slowCommand(cmds, { flap: "TL", factor: bad }), null);
  assert.equal(clearCommand(cmds).name, "Clear");
  assert.equal(clearCommand([]), null);
});

test("the last-flight preset is TL, armed for the descent, 42 degrees", () => {
  assert.deepEqual({ ...LAST_FLIGHT_PRESET }, { flap: "TL", armed: true, atCurrent: false, deg: 42 });
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
