import test from "node:test";
import assert from "node:assert/strict";
import { MissionTimeFeed } from "../app/status/mission_time_display.js";

const ms = (t, tm, phase) => ({ type: "MissionState", src: "usb", t, f: { t_mission: tm, mission_phase: phase } });

test("no mission seen: em dash value (null)", () => {
  const f = new MissionTimeFeed();
  assert.equal(f.missionTime({ t: 1000 }), null);
  assert.equal(f.missionTime(null), null);
  f.feed({ type: "VehicleStatus", t: 1, f: {} });
  assert.equal(f.missionTime({ t: 1 }), null);
});

test("continuous mission time over steps whose t_mission restarts", () => {
  const f = new MissionTimeFeed();
  for (let i = 0; i <= 10; i++) f.feed(ms(1000 + i * 0.5, -5 + i * 0.5, "Idle")); // countdown, T0 at 1005
  assert.ok(Math.abs(f.missionTime({ t: 1002 }) - -3) < 1e-9, "T-minus in the countdown");
  for (let i = 0; i < 8; i++) f.feed(ms(1005 + i * 0.5, i * 0.5, "Hover"));
  for (let i = 0; i < 8; i++) f.feed(ms(1009 + i * 0.5, i * 0.5, "Ascent")); // the step clock restarted at 0
  assert.ok(Math.abs(f.missionTime({ t: 1011 }) - 6) < 1e-9, "6 s since T0, the step value would be 2");
});

test("a new mission starts a new clock; samples are thinned and out-of-order ones ignored", () => {
  const f = new MissionTimeFeed();
  assert.equal(f.feed(ms(1000, -2, "Idle")), true);
  assert.equal(f.feed(ms(1000.2, -1.8, "Idle")), false, "within the sample gap");
  assert.equal(f.feed(ms(1000.3, -1.7, "Hover")), true, "a phase change is always taken");
  assert.equal(f.feed(ms(900, 0, "Hover")), false, "out of order");
  f.feed(ms(1002, 0, "Hover"));
  f.feed(ms(1003, 1, "Completed"));
  for (let i = 0; i <= 4; i++) f.feed(ms(2000 + i, -4 + i, "Idle"));
  assert.ok(Math.abs(f.missionTime({ t: 2001 }) - -3) < 1e-9);
  assert.ok(Math.abs(f.missionTime({ t: 1001 }) - -1) < 1e-9, "old mission keeps its own T0");
});

test("feedAll sorts, reset clears", () => {
  const f = new MissionTimeFeed();
  f.feedAll([ms(1001, -1, "Idle"), ms(1000, -2, "Idle"), ms(1002, 0, "Hover")]);
  assert.ok(Math.abs(f.missionTime({ t: 1002 })) < 1e-9);
  f.reset();
  assert.equal(f.missionTime({ t: 1002 }), null);
});

test("mission 2 resets the Mission panel clock, also without Completed (mode and countdown rules)", () => {
  const f = new MissionTimeFeed();
  const p = (t, tm, phase, mode) => ({ type: "MissionState", src: "usb", t, f: { t_mission: tm, mission_phase: phase, mission_mode: mode } });
  for (let i = 0; i <= 12; i++) f.feed(p(1000 + i, -5 + i, "Hover", "Startup")); // T0 at 1005
  for (let i = 0; i < 20; i++) f.feed(p(1012 + i, i, "Ascent", "Running")); // reads T+26 at 1031, mission never completes
  assert.ok(Math.abs(f.missionTime({ t: 1031 }) - 26) < 1e-9);
  assert.equal(f.feed(p(1032, 3, "Ascent", "Finished")), true, "a mode change is always taken");
  f.feed(p(1033, 3, "Ascent", "Initialisation"));
  f.feed(p(1034, -10, "Hover", "Initialisation"));
  f.feed(p(1035, -9, "Hover", "Startup"));
  assert.ok(Math.abs(f.missionTime({ t: 1035 }) - -9) < 1e-9, "new T-minus countdown, not T+30");
  assert.equal(f.clock.missions.length, 2);
});
