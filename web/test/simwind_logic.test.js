import test from "node:test";
import assert from "node:assert/strict";
import { SEQUENCE_SPACING_MS, WIND_NAMES, applySequence, clearSequence, degToVehicleRad, parseNumber, runSequence, vehicleRadToDeg, windState, wrapPi } from "../app/telecommand/simwind_logic.js";

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);

test("toward mode: counterclockwise from North, 90 degrees is toward West (pi/2)", () => {
  near(degToVehicleRad(0, "toward"), 0);
  near(degToVehicleRad(90, "toward"), Math.PI / 2);
  near(degToVehicleRad(270, "toward"), -Math.PI / 2);
  near(degToVehicleRad(180, "toward"), Math.PI);
});

test("from mode: compass bearing the wind comes from, clockwise", () => {
  near(degToVehicleRad(90, "from"), Math.PI / 2); // from East: air moves toward West
  near(Math.abs(degToVehicleRad(0, "from")), Math.PI); // from North: toward South
  near(degToVehicleRad(180, "from"), 0); // from South: toward North
  near(degToVehicleRad(270, "from"), -Math.PI / 2); // from West: toward East
});

test("wrap to +-pi and round trip", () => {
  near(wrapPi(3 * Math.PI), Math.PI);
  near(wrapPi(-2.5 * Math.PI), -Math.PI / 2);
  assert.ok(Math.abs(degToVehicleRad(725, "toward")) <= Math.PI);
  for (const m of ["from", "toward"]) for (const d of [10, 95, 200, 350]) near((((vehicleRadToDeg(degToVehicleRad(d, m), m) % 360) + 360) % 360), d);
});

test("parseNumber is strict and range checked", () => {
  assert.equal(parseNumber("5", 0, 20), 5);
  assert.equal(parseNumber("21", 0, 20), null);
  assert.equal(parseNumber("", 0, 20), null);
  assert.equal(parseNumber("NaN", 0, 20), null);
  assert.equal(parseNumber("1e2", 0, 200), null);
});

test("apply sequence is the five types in order; clear is speed 0 and gust 0", () => {
  const seq = applySequence({ speed: 5, directionRad: 1, gust: 3, period: 4, seed: 42 });
  assert.deepEqual(seq.map((s) => s.name), WIND_NAMES);
  assert.deepEqual(seq.map((s) => s.value), [5, 1, 3, 4, 42]);
  assert.equal(applySequence({ speed: 5, directionRad: null, gust: 3, period: 4, seed: 42 }), null);
  assert.deepEqual(clearSequence(), [{ name: "SimWindSpeed", value: 0 }, { name: "SimGustStrength", value: 0 }]);
});

test("sequence timing: one send each, 150 ms apart, none before the first", async () => {
  const log = [];
  let clock = 0;
  const res = await runSequence(applySequence({ speed: 1, directionRad: 0, gust: 0, period: 4, seed: 1 }),
    async (s) => { log.push([clock, s.name]); }, async (ms) => { clock += ms; });
  assert.equal(SEQUENCE_SPACING_MS, 150);
  assert.deepEqual(log.map((l) => l[0]), [0, 150, 300, 450, 600]);
  assert.equal(res.sent, 5);
  assert.equal(res.error, null);
});

test("sequence stops at the first refusal and never retries", async () => {
  let calls = 0;
  const res = await runSequence(clearSequence().concat(clearSequence()), async () => { calls++; if (calls === 2) throw new Error("rate_limited"); }, async () => {});
  assert.equal(calls, 2);
  assert.equal(res.sent, 1);
  assert.equal(res.error.message, "rate_limited");
});

test("enabled only in live view, with control, a route and the vehicle reporting simulation", () => {
  const ok = { apiState: "open", view: "live", canControl: true, route: "usb", simulation: true };
  assert.equal(windState(ok).enabled, true);
  for (const patch of [{ simulation: false }, { simulation: null }, { view: "replay" }, { canControl: false }, { route: null }, { apiState: "closed" }, { inputsOk: false }, { busy: true }])
    assert.equal(windState({ ...ok, ...patch }).enabled, false, JSON.stringify(patch));
});
