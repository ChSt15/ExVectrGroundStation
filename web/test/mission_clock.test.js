import test from "node:test";
import assert from "node:assert/strict";
import { MissionClock, buildMissions } from "../app/status/mission_clock.js";

const near = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} vs ${b}`);

/** Feed samples at 2 Hz: seq = [[tm, phase], ...] starting at sample time t. */
function feed(c, t, seq) {
  seq.forEach(([tm, phase], i) => c.add(t + i * 0.5, tm, phase));
}
const ramp = (from, to, phase) => {
  const out = [];
  for (let v = from; v <= to + 1e-9; v += 0.5) out.push([v, phase]);
  return out;
};

test("countdown into mission: T- before T0, T+ after, projected T0 while counting down", () => {
  const c = new MissionClock();
  feed(c, 1000, ramp(-10, -5, "Idle")); // countdown still running at the end of the data
  let m = c.missions;
  assert.equal(m.length, 1);
  assert.equal(m[0].t0Known, false);
  near(c.missionTime(1002.5), -7.5);
  near(m[0].t0, 1010); // projected
  feed(c, 1005.5, ramp(-4.5, 5, "Hover"));
  m = c.missions;
  assert.equal(m[0].t0Known, true);
  near(m[0].t0, 1010);
  near(c.missionTime(1010), 0);
  near(c.missionTime(1012), 2);
  near(c.missionTime(990), -20); // before the data: extrapolated
  assert.equal(m[0].partial, false);
  assert.equal(m[0].end, null);
});

test("phase changes mid-mission do not reset the clock (t_mission restarts per step)", () => {
  const c = new MissionClock();
  feed(c, 1000, ramp(-5, 0, "Idle"));
  feed(c, 1005.5, ramp(0.5, 10, "Hover")); // step 1 clock to 10 s
  feed(c, 1015.5, ramp(0, 10, "Ascent")); // step 2 restarts at 0
  feed(c, 1026, ramp(-2, 3, "Descent")); // even a negative step clock is just a step
  const m = c.missions;
  assert.equal(m.length, 1);
  near(m[0].t0, 1005);
  near(c.missionTime(1015.5), 10.5);
  near(c.missionTime(1025), 20);
  near(c.missionTime(1030), 25);
});

test("two missions in one recording, each with its own T0; the ends are reported", () => {
  const c = new MissionClock();
  feed(c, 1000, ramp(-5, 0, "Idle"));
  feed(c, 1005.5, ramp(0.5, 8, "Ascent"));
  c.add(1013.5, 8.5, "Completed");
  c.add(1014, null, "Idle");
  c.add(1014.5, null, "Idle");
  feed(c, 2000, ramp(-4, 0, "Idle")); // second countdown, new T0 = 2004
  feed(c, 2004.5, ramp(0.5, 3, "Hover"));
  const s = c.starts();
  assert.equal(s.length, 2);
  near(s[0].t0, 1005);
  assert.equal(s[0].endReason, "completed");
  near(s[0].end, 1013.5);
  near(s[1].t0, 2004);
  assert.equal(s[1].end, null);
  near(c.missionTime(1010), 5);
  near(c.missionTime(1500), 495); // between missions: the earlier one continues
  near(c.missionTime(2000), -4);
  near(c.missionTime(2006), 2);
  near(c.toSample(0, 2001), 2004);
  near(c.toSample(0, 1008), 1005);
});

test("reset to Idle and a new countdown start a new mission", () => {
  const c = new MissionClock();
  feed(c, 100, ramp(-2, 3, "Idle").map(([v], i) => [v, v < 0 ? "Idle" : "Hover"]));
  c.add(106, 3.5, "Idle"); // reset while running
  feed(c, 106.5, ramp(-3, 1, "Idle"));
  const m = c.missions;
  assert.equal(m.length, 2);
  assert.equal(m[0].endReason, "reset");
  near(m[1].t0, 109.5);
});

test("a clock gap or silence ends a mission; the clock returning mid-phase keeps the old T0", () => {
  const c = new MissionClock();
  feed(c, 100, ramp(-2, 6, "Idle").map(([v], i) => [v, v < 0 ? "Idle" : "Ascent"]));
  c.add(120, 22, "Ascent"); // 12 s without samples
  const m = c.missions;
  assert.equal(m.length, 1);
  assert.equal(m[0].endReason, "clock lost");
  near(c.missionTime(120), 18);
  const d = new MissionClock();
  feed(d, 0, ramp(-1, 2, "Idle").map(([v]) => [v, v < 0 ? "Idle" : "Hover"]));
  for (let i = 0; i < 20; i++) d.add(5 + i, null, "Hover"); // clock null for 20 s
  assert.equal(d.missions[0].endReason, "clock lost");
  const e = new MissionClock();
  feed(e, 0, ramp(1, 3, "Hover"));
  e.add(3.5, null, "Hover"); // a short null does not end it
  e.add(4, 3.5, "Hover");
  assert.equal(e.missions[0].end, null);
});

test("no mission: null mapping and no coverage; idle samples alone do not start one", () => {
  const c = new MissionClock();
  assert.equal(c.missionTime(5), null);
  assert.equal(c.toSample(0, 5), null);
  for (let i = 0; i < 10; i++) c.add(i, null, "Idle");
  for (let i = 10; i < 20; i++) c.add(i, 5, "Idle"); // a clock reading with Idle and no countdown: no mission
  assert.deepEqual(c.missions, []);
  assert.equal(c.missionTime(5), null);
  assert.equal(c.covers(0, 20), false);
});

test("replay seek: the clock comes from the history, not an accumulator; joining mid-mission is partial", () => {
  const all = [];
  const push = (t, tm, phase) => all.push({ t, tm, phase });
  for (let i = 0; i <= 20; i++) push(100 + i * 0.5, -5 + i * 0.5, i <= 10 ? "Idle" : "Hover"); // T0 = 105
  for (let i = 0; i <= 20; i++) push(110.5 + i * 0.5, i * 0.5, "Ascent"); // step 2 restarts
  const whole = buildMissions(all);
  const seeked = buildMissions(all.filter((s) => s.t >= 112)); // history loaded after a seek into step 2
  assert.equal(whole[0].partial, false);
  near(whole[0].t0, 105);
  assert.equal(seeked.length, 1);
  assert.equal(seeked[0].partial, true);
  near(seeked[0].t0, 110.5); // only the step clock is known there
  // the same samples give the same answer in any order of loading
  const c = new MissionClock();
  all.forEach((s) => c.add(s.t, s.tm, s.phase));
  near(c.missionTime(112), 7);
  assert.equal(c.covers(100, 130), true);
});

test("old out-of-order samples are dropped; capacity is bounded", () => {
  const c = new MissionClock(5);
  for (let i = 0; i < 8; i++) c.add(i, -10 + i, "Idle");
  assert.equal(c.samples.length, 5);
  assert.equal(c.add(1, 0, "Idle"), false);
});
