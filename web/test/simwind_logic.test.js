import test from "node:test";
import assert from "node:assert/strict";
import { WIND_ACTIONS, applySequence, clearSequence, degToVehicleRad, parseNumber, runRequestSequence, vehicleRadToDeg, windState, wrapPi } from "../app/simtests/simwind_logic.js";

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

test("apply sequence is the five actions in order; the seed goes in arg_i; clear is speed 0 and gust 0", () => {
  const seq = applySequence({ speed: 5, directionRad: 1, gust: 3, period: 4, seed: 42 });
  assert.deepEqual(seq.map((s) => s.action), WIND_ACTIONS);
  assert.deepEqual(seq.map((s) => s.arg_f ?? s.arg_i), [5, 1, 3, 4, 42]);
  assert.equal(seq[4].arg_i, 42);
  assert.equal(applySequence({ speed: 5, directionRad: null, gust: 3, period: 4, seed: 42 }), null);
  assert.deepEqual(clearSequence(), [{ action: "SIM_WIND_SPEED", arg_f: 0 }, { action: "SIM_GUST_STRENGTH", arg_f: 0 }]);
});

// a fake core: send returns a state, status reports it as acked after `pollsToAck` polls with the given status
function core({ pollsToAck = 1, statusFor = () => ["DONE", 0] } = {}) {
  const log = [];
  let clock = 0;
  const pending = new Map();
  return {
    log,
    sleep: async (ms) => { clock += ms; },
    send: async (s) => { log.push({ at: clock, ...s }); const id = log.length; pending.set(id, { polls: 0, s }); return { req_id: id, attempts: 1 }; },
    status: async () => {
      const recent = [], inflight = [];
      for (const [id, p] of pending) {
        p.polls++;
        if (p.polls > pollsToAck) { const [sn, d] = statusFor(p.s); recent.push({ req_id: id, action: p.s.action, state: "acked", status_name: sn, detail: d, attempts: 1 }); }
        else inflight.push({ req_id: id, action: p.s.action, state: "sending", attempts: 1, max_attempts: 5 });
      }
      return { inflight, recent };
    },
  };
}

test("sequence is paced: each request waits for its ACK, the next follows 550 ms later, none before the first", async () => {
  const c = core({ pollsToAck: 2 });
  const res = await runRequestSequence(applySequence({ speed: 1, directionRad: 0, gust: 0, period: 4, seed: 1 }), c);
  assert.equal(res.done, 5);
  assert.equal(res.error, null);
  assert.equal(c.log.length, 5);
  // 2 unanswered polls of 200 ms, then 550 ms spacing: 950 ms per step, and never two sends at the same time
  assert.deepEqual(c.log.map((l) => l.at), [0, 950, 1900, 2850, 3800]);
});

test("sequence stops at the first refusal with plain words, and never retries", async () => {
  const c = core({ statusFor: (s) => (s.action === "SIM_GUST_STRENGTH" ? ["REJECTED_STATE", 5] : ["DONE", 0]) });
  const res = await runRequestSequence(applySequence({ speed: 1, directionRad: 0, gust: 3, period: 4, seed: 1 }), c);
  assert.equal(res.done, 2);
  assert.equal(c.log.length, 3);
  assert.match(res.error.text, /not in simulation mode/);
});

test("a send error (rate limit, no route) and a missing answer both stop the sequence", async () => {
  let calls = 0;
  const err = await runRequestSequence(clearSequence(), { ...core(), send: async () => { calls++; throw new Error("rate_limited"); } });
  assert.equal(calls, 1);
  assert.equal(err.done, 0);
  assert.match(err.error.text, /rate_limited/);
  const none = await runRequestSequence(clearSequence(), { ...core(), status: async () => ({ inflight: [], recent: [] }), timeoutMs: 1000 });
  assert.equal(none.done, 0);
  assert.match(none.error.text, /TIMEOUT/);
});

test("enabled only in live view, with control, a route and the vehicle reporting simulation", () => {
  const ok = { apiState: "open", view: "live", canControl: true, route: "usb", simulation: true };
  assert.equal(windState(ok).enabled, true);
  for (const patch of [{ simulation: false }, { simulation: null }, { view: "replay" }, { canControl: false }, { route: null }, { apiState: "closed" }, { inputsOk: false }, { busy: true }])
    assert.equal(windState({ ...ok, ...patch }).enabled, false, JSON.stringify(patch));
});
