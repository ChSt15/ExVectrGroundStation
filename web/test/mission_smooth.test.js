import test from "node:test";
import assert from "node:assert/strict";
import { SmoothClock, SNAP_S, MAX_RUN_S } from "../app/status/mission_smooth.js";

const near = (a, b, e = 1e-6) => assert.ok(Math.abs(a - b) < e, `${a} vs ${b}`);

test("null before the first sample; the first sample is shown as is", () => {
  const c = new SmoothClock();
  assert.equal(c.value(0), null);
  near(c.push(-5, true, 1000), -5);
});

test("runs at 1 s/s between samples", () => {
  const c = new SmoothClock();
  c.push(10, true, 0);
  near(c.value(300), 10.3);
  near(c.value(900), 10.9);
});

test("stops extrapolating after MAX_RUN_S without a sample", () => {
  const c = new SmoothClock();
  c.push(10, true, 0);
  near(c.value(60000), 10 + MAX_RUN_S);
});

test("a late display is caught up gently, an early one slowed down, never backwards", () => {
  const c = new SmoothClock();
  c.push(10, true, 0);
  c.push(10.5 + 0.3, true, 500); // telemetry 0.3 s ahead of the display (10.5)
  const a = c.value(500);
  near(a, 10.5);
  let last = a;
  for (let ms = 520; ms <= 1500; ms += 20) {
    const v = c.value(ms);
    assert.ok(v >= last - 1e-12, "never backwards");
    last = v;
  }
  near(c.value(1000), 10.8 + 0.5); // error fully absorbed after 0.5 s: telemetry value + elapsed
  // early display: telemetry 0.4 s behind -> slowed down but still moving forward
  const d = new SmoothClock();
  d.push(20, true, 0);
  d.push(20.1, true, 500); // display 20.5, telemetry 20.1
  assert.ok(d.value(600) >= d.value(500));
  assert.ok(d.value(1000) < 20.5 + 0.5);
});

test("snaps on a reset (jump back), on an error above SNAP_S, and when the clock stops or starts", () => {
  const c = new SmoothClock();
  c.push(30, true, 0);
  near(c.push(-8, true, 200), -8); // new mission
  near(c.push(-8 + 0.2 + SNAP_S + 0.5, true, 400), -8 + 0.2 + SNAP_S + 0.5);
  near(c.push(50, false, 600), 50); // held final value
  near(c.value(5000), 50); // frozen
  near(c.push(50, false, 5000), 50);
  near(c.push(-4, true, 6000), -4);
});

test("NaN / null (no mission) clears the display", () => {
  const c = new SmoothClock();
  c.push(3, true, 0);
  assert.equal(c.push(null, false, 100), null);
  assert.equal(c.value(200), null);
});
