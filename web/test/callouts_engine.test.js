import test from "node:test";
import assert from "node:assert/strict";
import { CalloutEngine } from "../app/callouts/engine.js";
import { DEFAULTS, mergeSettings } from "../app/callouts/settings.js";

const cfg = (over) => mergeSettings(DEFAULTS, over);
const entry = (type, f, t) => ({ type, src: "usb", t, f });

/** Drive an engine with a mutable world; `step(dt, patch)` advances the view time and returns the events. */
function rig(over = {}, view = "live") {
  const c = cfg(over);
  const e = new CalloutEngine(() => c);
  const w = { now: 100, vs: { mission_mode: "Running", mission_phase: "Hover", armed: true, simulation: true, motors_enabled: false, failsafe: false, failure_bits: 0, zeroing: false }, ms: { mission_mode: "Running", mission_phase: "Hover", t_mission: 10, sp_pos: [0, 0, 10] }, pos: { pos: [0, 0, 10] }, sources: [], feed: { vs: true, ms: true, pos: true } };
  const run = () => e.evaluate({
    view, now: w.now, sources: w.sources,
    status: w.feed.vs ? entry("VehicleStatus", w.vs, w.now) : entry("VehicleStatus", w.vs, w.last_vs ?? 0),
    mission: w.feed.ms ? entry("MissionState", w.ms, w.now) : undefined,
    position: w.feed.pos ? entry("Position", w.pos, w.now) : undefined,
  });
  const step = (dt, patch = {}) => {
    w.now += dt;
    if (patch.vs) Object.assign(w.vs, patch.vs);
    if (patch.ms) Object.assign(w.ms, patch.ms);
    if (patch.pos) Object.assign(w.pos, patch.pos);
    return run();
  };
  return { e, w, run, step, c };
}
const texts = (r) => r.events.map((x) => x.text);

test("first pass announces sim/real once and nothing else (no state callouts for existing state)", () => {
  const { run, step } = rig();
  assert.deepEqual(texts(run()), ["Simulation"]);
  assert.deepEqual(texts(step(0.1)), []);
  assert.deepEqual(texts(step(0.1, { vs: { simulation: false } })), ["Real sensors"]);
});

test("countdown: 5..1 once each as the mission time crosses, Mission start at 0", () => {
  const { run, step, w } = rig();
  w.ms.t_mission = -6;
  run();
  const said = [];
  for (let i = 0; i < 70; i++) {
    const r = step(0.1, { ms: { t_mission: w.ms.t_mission + 0.1 } });
    said.push(...texts(r));
  }
  assert.deepEqual(said, ["5", "4", "3", "2", "1", "Mission start"]);
  // staying above zero repeats nothing
  assert.deepEqual(texts(step(0.1, { ms: { t_mission: 1 } })), []);
});

test("countdown: a time jump or a mission time reset re-baselines silently", () => {
  const { run, step, w } = rig();
  w.ms.t_mission = -20;
  run();
  assert.deepEqual(texts(step(0.1, { ms: { t_mission: -2 } })), []); // jumped past -5..-3
  assert.deepEqual(texts(step(0.1, { ms: { t_mission: -20 } })), []); // reset backwards
  assert.deepEqual(texts(step(0.1, { ms: { t_mission: -19.9 } })), []);
});

test("phase edges: names from the vehicle phases, first observation silent, Idle silent", () => {
  const { run, step, w } = rig();
  w.ms.mission_phase = "Idle";
  assert.deepEqual(texts(run()).filter((t) => t !== "Simulation"), []);
  const seq = ["Hover", "Ascent", "Descent", "Deceleration", "RTH", "Landing", "Completed", "Idle"];
  const said = seq.flatMap((p) => texts(step(0.2, { ms: { mission_phase: p } })));
  assert.deepEqual(said, ["Hover", "Ascent", "Belly flop", "Deceleration", "Return to home", "Landing", "Landed"]);
});

test("mission begin: said when the mode leaves Idle; phase (Hover) only once Running", () => {
  const { run, step, w } = rig();
  w.vs.mission_mode = "Idle";
  w.ms.mission_mode = "Idle";
  w.ms.t_mission = -10;
  run();
  const both = (m) => ({ ms: { mission_mode: m }, vs: { mission_mode: m } });
  // the vehicle reports Hover from Ready on: nothing for it while Idle / Initialisation / Startup
  assert.deepEqual(texts(step(0.2, both("Initialisation"))), ["Mission beginning"]);
  assert.deepEqual(texts(step(0.2, both("Startup"))), []);
  assert.deepEqual(texts(step(0.2, { ms: { t_mission: -9 } })), []);
  assert.deepEqual(texts(step(0.2, both("Running"))), ["Hover"]);
  assert.deepEqual(texts(step(0.2, { ms: { mission_phase: "Ascent" } })), ["Ascent"]);
  assert.deepEqual(texts(step(0.2, both("Finished"))), []);
  assert.deepEqual(texts(step(0.2, { ms: { mission_phase: "Completed" } })), ["Landed"]);
});

test("mission begin: not said for the first observation or when connecting to a running mission", () => {
  const { run, step } = rig();
  assert.deepEqual(texts(run()), ["Simulation"]); // already Running at connect: silent, no Hover
  assert.deepEqual(texts(step(0.2)), []);
});

test("altitude: both directions, once per crossing, hysteresis stops repeats near a threshold", () => {
  const { run, step, w } = rig();
  w.pos.pos = [0, 0, 10];
  run();
  const alt = (z) => texts(step(0.1, { pos: { pos: [0, 0, z] } }));
  assert.deepEqual(alt(24), []);
  assert.deepEqual(alt(27), ["Altitude 25"]);
  // noise around 25 m (hysteresis 3 m: 1.5 m each side) does not repeat
  const noise = [24.9, 25.2, 24.6, 25.4, 24.0, 26.0, 25.0].flatMap(alt);
  assert.deepEqual(noise, []);
  assert.deepEqual(alt(60), ["Altitude 50"]);
  assert.deepEqual(alt(210), ["Altitude 200"]); // several levels in one step: only the last reached
  assert.deepEqual(alt(140), ["Altitude 150"]); // descending
  assert.deepEqual(alt(20), ["Altitude 25"]); // 100, 50 and 25 passed: the lowest is said
});

test("altitude descending says the lowest level crossed in a step; falling through a single level says it", () => {
  const { run, step, w } = rig();
  w.pos.pos = [0, 0, 120];
  run();
  const alt = (z) => texts(step(0.1, { pos: { pos: [0, 0, z] } }));
  assert.deepEqual(alt(90), ["Altitude 100"]);
  assert.deepEqual(alt(40), ["Altitude 50"]);
});

test("altitude is ignored on the ground (mission not Running)", () => {
  const { run, step, w } = rig();
  w.vs.mission_mode = "Idle";
  w.ms.mission_mode = "Idle";
  run();
  assert.deepEqual(texts(step(0.1, { pos: { pos: [0, 0, 60] } })), []);
  // going Running at 60 m only sets the baseline: nothing is said for levels already passed
  assert.deepEqual(texts(step(0.1, { ms: { mission_mode: "Running" }, vs: { mission_mode: "Running" } })), ["Mission beginning", "Hover"]);
  assert.deepEqual(texts(step(0.1, { pos: { pos: [0, 0, 110] } })), ["Altitude 100"]);
});

test("failure bits: one callout per bit, unknown bits named, failsafe, cleared, repeats every 5 s up to 3 times", () => {
  const { run, step } = rig();
  run();
  const r = step(0.2, { vs: { failure_bits: 0b100001 | (1 << 9) } });
  assert.deepEqual(texts(r), ["Sensor failure", "Control source lost", "Gyro failure"]);
  assert.ok(r.events.every((e) => e.severity === "failure" && e.priority === 0 && e.sticky));
  const names = ["Sensor failure", "Attitude failure", "Position failure", "Position out of bounds", "Attitude out of bounds", "Control source lost"];
  const { run: run2, step: step2 } = rig();
  run2();
  for (let i = 0; i < 6; i++) {
    const { texts: _t } = { texts };
    assert.deepEqual(texts(step2(0.1, { vs: { failure_bits: 1 << i } })).filter((t) => names.includes(t)), [names[i]]);
    step2(0.1, { vs: { failure_bits: 0 } });
  }
  // default: announced once, then silence while the failure persists
  const once = rig();
  once.run();
  const saidOnce = [...texts(once.step(0.1, { vs: { failure_bits: 4 } }))];
  for (let i = 0; i < 400; i++) saidOnce.push(...texts(once.step(0.1)));
  assert.deepEqual(saidOnce, ["Position failure"]);
  // with repeats configured: 4 announcements in total, then silence while the failure persists
  const rc = { failure: { repeat_s: 5, repeats: 3 } };
  const q = rig(rc);
  q.run();
  const said = [...texts(q.step(0.1, { vs: { failure_bits: 4 } }))];
  for (let i = 0; i < 400; i++) said.push(...texts(q.step(0.1)));
  assert.deepEqual(said, Array(4).fill("Position failure"));
  const rep = rig(rc);
  rep.run();
  rep.step(0.1, { vs: { failure_bits: 4 } });
  const t1 = rep.step(4.9);
  assert.equal(t1.events.length, 0);
  const t2 = rep.step(0.2);
  assert.equal(t2.events.length, 1);
  assert.equal(t2.events[0].repeat, true);
  // clearing
  const c = rig();
  c.run();
  c.step(0.1, { vs: { failsafe: true } });
  const cleared = c.step(0.1, { vs: { failsafe: false } });
  assert.deepEqual(texts(cleared), ["Failure cleared"]);
  assert.deepEqual(cleared.clears, ["failure:failsafe"]);
});

test("failsafe rising says Failsafe", () => {
  const { run, step } = rig();
  run();
  assert.deepEqual(texts(step(0.1, { vs: { failsafe: true } })), ["Failsafe"]);
});

test("state: motors armed, disarmed, motors live (said once, as a warning), zeroing and zero complete", () => {
  const { run, step } = rig();
  run();
  assert.deepEqual(texts(step(0.1, { vs: { armed: false } })), ["Motors disarmed"]);
  assert.deepEqual(texts(step(0.1, { vs: { armed: true } })), ["Motors armed"]);
  const m = step(0.1, { vs: { motors_enabled: true } });
  assert.deepEqual(texts(m), ["Motors live"]);
  assert.equal(m.events[0].severity, "warning");
  assert.ok(m.events[0].priority > 0);
  const said = [];
  for (let i = 0; i < 100; i++) said.push(...texts(step(0.1)));
  assert.deepEqual(said, []); // no repeat
  assert.deepEqual(texts(step(0.1, { vs: { zeroing: true } })), ["Zeroing"]);
  assert.deepEqual(texts(step(0.1, { vs: { zeroing: false } })), ["Zero complete"]);
});

test("stale live data never triggers mission or altitude callouts and re-baselines on return", () => {
  const { run, step, w } = rig();
  w.ms.t_mission = -3.5;
  run();
  w.feed.ms = true;
  // mission and position entries stop arriving: they age (the harness re-sends the same t via a frozen entry)
  const c = cfg();
  const e = new CalloutEngine(() => c);
  const base = { view: "live", now: 100, sources: [] };
  const ms = (tm, t) => entry("MissionState", { mission_mode: "Running", mission_phase: "Hover", t_mission: tm }, t);
  const pos = (z, t) => entry("Position", { pos: [0, 0, z] }, t);
  e.evaluate({ ...base, mission: ms(-5.5, 100), position: pos(10, 100) });
  // 5 s later the old entries are stale although the clock moved on: values that would cross are ignored
  assert.deepEqual(e.evaluate({ ...base, now: 105, mission: ms(-4.9, 100), position: pos(60, 100) }).events, []);
  // fresh again with later values: baseline only, nothing said for the gap
  assert.deepEqual(e.evaluate({ ...base, now: 106, mission: ms(-1.2, 106), position: pos(120, 106) }).events, []);
  const r = e.evaluate({ ...base, now: 107, mission: ms(0.1, 107), position: pos(130, 107) });
  assert.deepEqual(r.events.map((x) => x.text), ["Mission start"]);
});

test("telemetry lost after 3 s without VehicleStatus (live), restored when it returns; none in replay", () => {
  const { run, step, w } = rig();
  run();
  w.feed.vs = false;
  w.last_vs = w.now;
  assert.deepEqual(texts(step(2.5)), []);
  const lost = step(1);
  assert.deepEqual(texts(lost), ["Telemetry lost"]);
  assert.equal(lost.events[0].severity, "warning");
  assert.deepEqual(texts(step(3)), []); // once
  w.feed.vs = true;
  const back = step(0.2);
  assert.deepEqual(texts(back), ["Telemetry restored"]);
  assert.deepEqual(back.clears, ["telemetry"]);
  // replay: never
  const r = rig({}, "replay");
  r.run();
  r.w.feed.vs = false;
  r.w.last_vs = r.w.now;
  assert.deepEqual(texts(r.step(10)).filter((t) => t.startsWith("Telemetry")), []);
});

test("Router link lost when the Router source disconnects, not for a user-disabled or absent Router", () => {
  const { run, step, w } = rig();
  w.sources = [{ id: "link", kind: "link_module", state: "connected" }, { id: "usb", kind: "vehicle_usb", state: "connected" }];
  run();
  w.sources = [{ id: "link", kind: "link_module", state: "searching" }, { id: "usb", kind: "vehicle_usb", state: "connected" }];
  assert.deepEqual(texts(step(0.1)), ["Router link lost"]);
  assert.deepEqual(texts(step(0.1)), []);
  w.sources = [{ id: "link", kind: "link_module", state: "connected" }];
  assert.deepEqual(texts(step(0.1)), ["Router link restored"]);
  w.sources = [{ id: "link", kind: "link_module", state: "disabled" }];
  assert.deepEqual(texts(step(0.1)), []);
  const none = rig();
  none.w.sources = [{ id: "usb", kind: "vehicle_usb", state: "searching" }];
  none.run();
  assert.deepEqual(texts(none.step(0.1)), []);
});

test("replay seek: a jump in view time re-baselines silently; normal playback follows the timeline", () => {
  const { run, step, w } = rig({}, "replay");
  w.ms.t_mission = -6;
  run();
  const said = [];
  for (let i = 0; i < 20; i++) said.push(...texts(step(0.1, { ms: { t_mission: w.ms.t_mission + 0.1 } })));
  assert.deepEqual(said, ["5"]); // normal playback speaks
  // seek forward 30 s: phase, altitude, countdown, failure all differ, nothing is said
  const j = step(30, { ms: { mission_phase: "Descent", t_mission: 25 }, pos: { pos: [0, 0, 160] }, vs: { failure_bits: 2, armed: false } });
  assert.deepEqual(j.events, []);
  assert.equal(j.resynced, true);
  // seek back
  const b = step(-20, { ms: { mission_phase: "Ascent", t_mission: -3 }, pos: { pos: [0, 0, 5] }, vs: { failure_bits: 0, armed: true } });
  assert.deepEqual(b.events, []);
  // and playback continues normally afterwards
  assert.deepEqual(texts(step(0.1, { ms: { t_mission: -2.95 } })), []);
  const nxt = step(1, { ms: { t_mission: -1.95 } });
  assert.deepEqual(texts(nxt), ["2"]);
});

test("position deviation: needs the limit for the hold time, own hysteresis, fires once per excursion", () => {
  const { run, step, w } = rig({ categories: { deviation: true } });
  run();
  const dev = (z, dt = 0.5) => texts(step(dt, { pos: { pos: [0, 0, 10 + z] } }));
  assert.deepEqual(dev(0), []);
  assert.deepEqual(dev(6), []); // starts counting
  assert.deepEqual(dev(6), []);
  assert.deepEqual(dev(6), []);
  assert.deepEqual(dev(6), []);
  assert.deepEqual(dev(6, 0.5), ["Position deviation"]);
  assert.deepEqual(dev(6), []); // once
  assert.deepEqual(dev(4.5), []); // inside the limit but not below limit - hysteresis: still armed off
  assert.deepEqual(dev(6, 5), []); // no repeat
  assert.deepEqual(dev(0), []);
  for (let i = 0; i < 4; i++) dev(6);
  assert.deepEqual(dev(6, 0.5), ["Position deviation"]);
  // a short excursion does not fire
  const s = rig();
  s.run();
  s.step(0.5, { pos: { pos: [0, 0, 20] } });
  assert.deepEqual(texts(s.step(0.5, { pos: { pos: [0, 0, 10] } })), []);
  assert.equal(w.now > 0, true);
});

test("T9b-4: motors armed watchdog failure, SD toggle needed and a refused go (reason in the toast label)", () => {
  const { run, step } = rig();
  run();
  assert.deepEqual(texts(step(0.1, { vs: { failure_bits: 1 << 13, ctrl_flags: 0, go_refusal: 0 } })), ["Motors armed watchdog failure"]);
  const t = step(0.1, { vs: { failure_bits: 0, ctrl_flags: 1 } });
  assert.deepEqual(texts(t), ["Failure cleared", "Toggle SD off, then on"]);
  assert.deepEqual(texts(step(0.1, { vs: { ctrl_flags: 1 } })), []); // once per rising edge
  const g = step(0.1, { vs: { ctrl_flags: 0, go_refusal: 4 } });
  assert.deepEqual(texts(g), ["Go refused"]);
  assert.equal(g.events[0].label, "Go refused: SC invalid, reserved or different from the vehicle mode");
  assert.deepEqual(texts(step(0.1, { vs: { go_refusal: 4 } })), []);
});
