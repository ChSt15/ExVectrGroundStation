import test from "node:test";
import assert from "node:assert/strict";
import { HOLD_MS, headerButtons, panelState, buttonOps, safetyGuard } from "../app/command/button_logic.js";
import { HoldTimer } from "../app/radio/radio_logic.js";

const cmd = (o = {}) => ({ input: "none", out: { go: 0, motors: 0, vehicle_mode: 0, reset_button: 0 }, hints: {}, failsafe_latched: false, ...o });
const rc = (c, extra = {}) => ({ command: c, lease: null, holder_is_you: false, ...extra });

test("hold time is 500 ms and a release before it cancels", () => {
  let now = 0, fire = null, done = 0;
  const timers = { now: () => now, setTimeout: (fn) => { fire = fn; return 1; }, clearTimeout: () => { fire = null; }, setInterval() {}, clearInterval() {} };
  const t = new HoldTimer(() => done++, timers, HOLD_MS);
  assert.equal(HOLD_MS, 500);
  t.start(); now = 250;
  assert.equal(t.progress(), 0.5);
  t.cancel(); assert.equal(fire, null); assert.equal(done, 0);
  t.start(); now = 600; fire(); assert.equal(done, 1);
});

test("safety buttons are disabled only when the core is down, with the reason", () => {
  assert.deepEqual(safetyGuard({ open: false, rc: null, canControl: true }), { ok: false, why: "core offline" });
  const h = headerButtons({ open: false, rc: null, canControl: true });
  assert.ok(h.abort.disabled && h.failsafe.disabled && h.abort.title.includes("core offline"));
  for (const input of ["pocket", "virtual", "none"]) {
    const ok = headerButtons({ open: true, rc: rc(cmd({ input })), canControl: true });
    assert.ok(!ok.abort.disabled && !ok.failsafe.disabled, input);
  }
});

test("failsafe button turns into the clear control while the engine reports the latch", () => {
  const idle = headerButtons({ open: true, rc: rc(cmd()), canControl: true }).failsafe;
  assert.deepEqual([idle.label, idle.on, idle.banner], ["FAILSAFE", true, ""]);
  const lat = headerButtons({ open: true, rc: rc(cmd({ failsafe_latched: true })), canControl: true }).failsafe;
  assert.deepEqual([lat.label, lat.on, lat.banner, lat.state], ["Clear failsafe request", false, "FAILSAFE REQUESTED", "requested"]);
});

test("pocket live: panel buttons disabled except abort", () => {
  const p = panelState({ open: true, rc: rc(cmd({ input: "pocket" })), canControl: true });
  assert.equal(p.note, "Pocket in control");
  for (const k of ["begin", "motors", "real", "sim", "reset", "zero"]) assert.ok(p.buttons[k].disabled, k);
  assert.ok(!p.buttons.abort.disabled);
});

test("panel shows the engine's sent state and waits for a pickup", () => {
  const p = panelState({ open: true, rc: rc(cmd({ out: { go: 0, motors: 1, vehicle_mode: 1, reset_button: 0 }, hints: { sa: true } }), { lease: { holder: "me" }, holder_is_you: true }), canControl: true });
  assert.deepEqual(p.state, { go: "NOT GO", motors: "ARMED", mode: "SIMULATION", reset: "released" });
  assert.ok(p.buttons.begin.disabled && p.buttons.begin.why.includes("Abort first"));
  assert.ok(!p.buttons.motors.disabled && p.motorsAction === "disarm");
  assert.ok(p.buttons.sim.disabled && !p.buttons.real.disabled);
});

test("button request sequences", () => {
  const free = { holder: false }, mine = { holder: true };
  assert.deepEqual(buttonOps("begin", free).map((o) => o.op), ["rc.acquire", "rc.set"]);
  assert.deepEqual(buttonOps("abort", free), [{ op: "engine.abort" }]);
  assert.deepEqual(buttonOps("abort", mine).map((o) => o.op), ["engine.abort", "rc.set"]);
  assert.deepEqual(buttonOps("arm", mine).map((o) => o.op), ["rc.unlock", "rc.set"]);
  assert.deepEqual(buttonOps("reset", mine)[0], { op: "rc.set", values: { SE: 1 }, hold_s: 0.3 });
});
