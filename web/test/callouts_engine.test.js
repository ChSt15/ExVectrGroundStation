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
  assert.deepEqual(said, ["Hover", "Ascent", "Descent", "Deceleration", "Return to home", "Landing", "Landed"]);
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
  assert.deepEqual(alt(27), ["25"]);
  // noise around 25 m (hysteresis 3 m: 1.5 m each side) does not repeat
  const noise = [24.9, 25.2, 24.6, 25.4, 24.0, 26.0, 25.0].flatMap(alt);
  assert.deepEqual(noise, []);
  assert.deepEqual(alt(60), ["50"]);
  assert.deepEqual(alt(210), ["200"]); // several levels in one step: only the last reached
  assert.deepEqual(alt(140), ["150"]); // descending
  assert.deepEqual(alt(20), ["25"]); // 100, 50 and 25 passed: the lowest is said
});

test("altitude descending says the lowest level crossed in a step; falling through a single level says it", () => {
  const { run, step, w } = rig();
  w.pos.pos = [0, 0, 120];
  run();
  const alt = (z) => texts(step(0.1, { pos: { pos: [0, 0, z] } }));
  assert.deepEqual(alt(90), ["100"]);
  assert.deepEqual(alt(40), ["50"]);
});

test("altitude is ignored on the ground (mission not Running)", () => {
  const { run, step, w } = rig();
  w.vs.mission_mode = "Idle";
  w.ms.mission_mode = "Idle";
  run();
  assert.deepEqual(texts(step(0.1, { pos: { pos: [0, 0, 60] } })), []);
  // going Running at 60 m only sets the baseline: nothing is said for levels already passed
  assert.deepEqual(texts(step(0.1, { ms: { mission_mode: "Running" }, vs: { mission_mode: "Running" } })), ["Mission beginning", "Hover"]);
  assert.deepEqual(texts(step(0.1, { pos: { pos: [0, 0, 110] } })), ["100"]);
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

test("state: Motors armed / disarmed once each; Motors enabled, zeroing and zero complete are not announced", () => {
  const { run, step } = rig();
  run();
  assert.deepEqual(texts(step(0.1, { vs: { armed: false } })), ["Motors disarmed"]);
  assert.deepEqual(texts(step(0.1, { vs: { armed: true } })), ["Motors armed"]);
  assert.equal(step(0.1, { vs: { armed: true } }).events[0], undefined);
  assert.deepEqual(texts(step(0.1, { vs: { motors_enabled: true } })), []);
  assert.deepEqual(texts(step(0.1, { vs: { motors_enabled: false, zeroing: true } })), []);
  assert.deepEqual(texts(step(0.1, { vs: { zeroing: false } })), []);
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

test("T9b-4: motors armed watchdog failure, no callout for SD toggle needed, and a refused go (reason in the toast label)", () => {
  const { run, step } = rig();
  run();
  assert.deepEqual(texts(step(0.1, { vs: { failure_bits: 1 << 13, ctrl_flags: 0, go_refusal: 0 } })), ["Motors armed watchdog failure"]);
  const t = step(0.1, { vs: { failure_bits: 0, ctrl_flags: 1 } });
  assert.deepEqual(texts(t), ["Failure cleared"]); // INTERLOCK_NEED_TOGGLE (ctrl_flags bit 0): no callout
  assert.deepEqual(texts(step(0.1, { vs: { ctrl_flags: 1 } })), []);
  const g = step(0.1, { vs: { ctrl_flags: 0, go_refusal: 4 } });
  assert.deepEqual(texts(g), ["Go refused"]);
  assert.equal(g.events[0].label, "Go refused: SC invalid, reserved or different from the vehicle mode");
  assert.deepEqual(texts(step(0.1, { vs: { go_refusal: 4 } })), []);
});

/** A descent at `v` m/s from `z0` with a flip start moving with speed (flip = 20 + 2 v m, recomputed by the vehicle). */
function descent(over = {}, v = 7, z0 = 120) {
  const r = rig(over);
  const { w } = r;
  w.vs.mission_phase = w.ms.mission_phase = "Descent";
  w.ms.sp_pos = null;
  w.ms.flip_start_altitude = 20 + 2 * v;
  w.pos.pos = [0, 0, z0];
  w.pos.vel = [0, 0, -v];
  r.run();
  let z = z0;
  r.setZ = (v0) => (z = v0);
  r.fall = (until, vNow = v) => {
    const said = [];
    while (z > until) {
      z -= vNow * 0.1;
      said.push(...texts(r.step(0.1, { ms: { flip_start_altitude: 20 + 2 * vNow }, pos: { pos: [0, 0, z], vel: [0, 0, -vNow] } })));
    }
    return said;
  };
  return r;
}

test("freefall countdown: 5 4 3 2 1 once each at 7 m/s, no Begin, no distance wording, altitude callouts muted", () => {
  const r = descent();
  // flip start 34 m, so it is reached at z = 34
  assert.deepEqual(r.fall(30), ["5", "4", "3", "2", "1"]);
  assert.deepEqual(r.fall(10), []);
  // the upright is the phase change: "Deceleration" once, never "Begin"
  assert.deepEqual(texts(r.step(0.1, { vs: { mission_phase: "Deceleration" }, ms: { mission_phase: "Deceleration" } })), ["Deceleration"]);
});

test("freefall countdown: a flip start that moves with the speed is followed each tick", () => {
  const r = descent({}, 7, 120);
  const said = r.fall(80, 7); // far above, nothing yet
  assert.deepEqual(said, []);
  // the speed rises to 10 m/s: flip start 40 m, the countdown follows the new values
  const more = r.fall(40.5, 10);
  assert.deepEqual(more, ["5", "4", "3", "2", "1"]);
  assert.deepEqual(r.fall(30, 10), []);
});

test("freefall countdown: levels already passed at the first sample are skipped silently; several at once only the lowest; custom levels", () => {
  const r = descent({}, 7, 34 + 2.9 * 7); // 2.9 s to go: 5, 4, 3 passed
  assert.deepEqual(r.fall(30), ["2", "1"]);
  const c = descent({ freefall: { countdown_s: [3, 10] } }, 7, 34 + 9 * 7);
  assert.deepEqual(c.fall(30), ["3"]); // 10 passed at the start
  // a jump over 4, 3 and 2 in one step: only the lowest
  const j = descent({}, 7, 34 + 6 * 7);
  j.fall(34 + 4.5 * 7);
  const step = j.step(0.1, { pos: { pos: [0, 0, 34 + 1.5 * 7], vel: [0, 0, -7] } });
  assert.deepEqual(texts(step).filter((t) => t !== "5"), ["2"]);
});

test("freefall countdown: no flip start (NaN, null, missing) is silent; it follows a later value", () => {
  for (const none of [NaN, null, undefined]) {
    const r = descent({}, 7, 120);
    r.w.ms.flip_start_altitude = none;
    const said = [];
    for (let z = 119; z >= 5; z -= 0.7) said.push(...texts(r.step(0.1, { ms: { flip_start_altitude: none }, pos: { pos: [0, 0, z], vel: [0, 0, -7] } })));
    assert.deepEqual(said, []);
  }
  const r = descent({}, 7, 100);
  r.w.ms.flip_start_altitude = NaN;
  r.step(0.1, { ms: { flip_start_altitude: NaN }, pos: { pos: [0, 0, 99.3] } });
  r.step(0.1, { ms: { flip_start_altitude: 34 }, pos: { pos: [0, 0, 98.6] } }); // appears: baseline, silent
  r.setZ(98.6);
  assert.deepEqual(r.fall(75), []);
  assert.deepEqual(r.fall(36), ["5", "4", "3", "2", "1"]);
});

test("freefall countdown: a slow descent (below 0.5 m/s) or missing velocity is silent", () => {
  const slow = descent({}, 0.3, 50);
  slow.w.ms.flip_start_altitude = 20;
  assert.deepEqual(slow.fall(25, 0.3), []);
  const r = rig();
  r.w.vs.mission_phase = r.w.ms.mission_phase = "Descent";
  r.w.ms.sp_pos = null;
  r.w.ms.flip_start_altitude = 20;
  r.w.pos.pos = [0, 0, 80];
  r.run();
  const said = [];
  for (let z = 79; z > 21; z--) said.push(...texts(r.step(0.1, { pos: { pos: [0, 0, z] } })));
  assert.deepEqual(said, []); // no vertical speed in the telemetry
});

test("freefall countdown: the speed is smoothed (a single noisy sample does not skip or repeat)", () => {
  const r = descent({}, 7, 34 + 6 * 7);
  const said = [];
  let z = 34 + 6 * 7;
  for (let i = 0; i < 80 && z > 33; i++) {
    z -= 0.7;
    const noisy = i % 3 === 0 ? -9 : -6; // mean around 7
    said.push(...texts(r.step(0.1, { pos: { pos: [0, 0, z], vel: [0, 0, noisy] } })));
  }
  assert.deepEqual(said, ["5", "4", "3", "2", "1"].slice(5 - said.length));
  assert.equal(new Set(said).size, said.length);
});

test("freefall countdown: Deceleration from the phase change (flip start NaN) and no Begin; a new descent re-arms", () => {
  const r = descent({}, 7, 60);
  r.fall(55);
  const t = texts(r.step(0.1, { vs: { mission_phase: "Deceleration" }, ms: { mission_phase: "Deceleration", flip_start_altitude: NaN } }));
  assert.deepEqual(t, ["Deceleration"]);
  r.step(0.1, { vs: { mission_phase: "Landing" }, ms: { mission_phase: "Landing" } });
  r.step(0.1, { vs: { mission_phase: "Descent" }, ms: { mission_phase: "Descent", flip_start_altitude: 34 }, pos: { pos: [0, 0, 120], vel: [0, 0, -7] } });
  r.setZ(120);
  assert.deepEqual(r.fall(30), ["5", "4", "3", "2", "1"]);
});

test("freefall settings: an old saved object with metre levels loads and is ignored", () => {
  const c = mergeSettings(DEFAULTS, { freefall: { levels: [5, 10, 15, 25, 50] } });
  assert.deepEqual(c.freefall, { countdown_s: [1, 2, 3, 4, 5] });
  assert.equal("levels" in c.freefall, false);
  assert.deepEqual(mergeSettings(DEFAULTS, { freefall: { countdown_s: [10, 3] } }).freefall.countdown_s, [3, 10]);
});

test("a stored settings object with the removed ground_distance section is ignored", () => {
  const m = mergeSettings(DEFAULTS, { ground_distance: { enabled: true, repeat_s: 1 } });
  assert.equal("ground_distance" in m, false);
});

test("apogee: said once per flight when the smoothed vertical speed goes to <= 0 in Ascent above 5 m; re-armed by the next mission begin", () => {
  const { step, run, w } = rig();
  w.ms.mission_phase = "Ascent";
  w.vs.mission_phase = "Ascent";
  w.pos.pos = [0, 0, 40];
  w.pos.vel = [0, 0, 8];
  run();
  const said = [];
  const go = (z, vz, n = 1) => { for (let i = 0; i < n; i++) said.push(...texts(step(0.1, { pos: { pos: [0, 0, z], vel: [0, 0, vz] } })).filter((t) => t === "Apogee")); };
  go(50, 8, 10);
  go(60, 1, 2);
  assert.deepEqual(said, []); // still rising on the 0.3 s average
  go(61, -3, 6);
  assert.deepEqual(said, ["Apogee"]);
  go(61, 2, 6);
  go(62, -2, 6);
  assert.deepEqual(said, ["Apogee"]); // once per flight
  // next flight: mission ends and begins again
  step(0.1, { vs: { mission_mode: "Idle" }, ms: { mission_mode: "Idle" } });
  step(0.1, { vs: { mission_mode: "Running" }, ms: { mission_mode: "Running" } });
  said.length = 0;
  go(50, 8, 10);
  go(60, -2, 6);
  assert.deepEqual(said, ["Apogee"]);
});

test("apogee: not below 5 m, not outside Ascent, not without a rising phase first", () => {
  const { step, run, w } = rig();
  w.ms.mission_phase = w.vs.mission_phase = "Ascent";
  w.pos.pos = [0, 0, 3];
  w.pos.vel = [0, 0, 4];
  run();
  const said = [];
  const go = (z, vz, n = 1, ph) => { for (let i = 0; i < n; i++) said.push(...texts(step(0.1, { pos: { pos: [0, 0, z], vel: [0, 0, vz] }, ...(ph ? { vs: { mission_phase: ph }, ms: { mission_phase: ph } } : {}) }))); };
  go(3, 4, 5); go(4, -1, 6); // hop below 5 m
  go(30, -1, 6); // falling from the start of the detection: never rising, silent
  go(30, 5, 6, "Hover"); go(40, -5, 6); // Hover phase is not Ascent
  assert.deepEqual(said.filter((t) => t === "Apogee"), []);
});

test("final callout set: Ascent, Descent, Deceleration, Landing, Landed, Return to home each once; no Belly flop / Begin", () => {
  const { step, run, w } = rig();
  w.ms.mission_phase = "Hover";
  run();
  const said = [];
  for (const p of ["Ascent", "Descent", "Deceleration", "RTH", "Landing", "Completed"]) said.push(...texts(step(0.2, { ms: { mission_phase: p }, vs: { mission_phase: p } })));
  assert.deepEqual(said, ["Ascent", "Descent", "Deceleration", "Return to home", "Landing", "Landed"]);
});

test("settings migration: a saved 'Belly flop' phase name becomes Descent", () => {
  assert.equal(mergeSettings(DEFAULTS, { phaseNames: { Descent: "Belly flop" } }).phaseNames.Descent, "Descent");
  assert.equal(mergeSettings(DEFAULTS, { phaseNames: { Descent: "Dive" } }).phaseNames.Descent, "Dive");
});
