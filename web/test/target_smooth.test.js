import test from "node:test";
import assert from "node:assert/strict";
import { TargetSmoother } from "../app/view3d/target_smooth.js";
import { UI_SCALES, cleanDisplay, uiZoom, pixelRatio, MAX_PIXEL_RATIO } from "../app/display_settings.js";

const near = (a, b, e = 1e-9) => a.every((x, i) => Math.abs(x - b[i]) < e);

const feed = (s, t0, t1, hz, f) => { // packets at `hz` from t0 to t1; returns the drawn positions at 60 Hz
  const out = [];
  let nextPkt = t0;
  for (let t = t0; t <= t1 + 1e-9; t += 1 / 60) {
    while (nextPkt <= t + 1e-9) {
      const [p, v] = f(nextPkt);
      s.update(nextPkt, p, v, nextPkt);
      nextPkt += 1 / hz;
    }
    out.push([t, s.at(t)]);
  }
  return out;
};

test("target: first packet is drawn as received, hold when nothing moves", () => {
  const s = new TargetSmoother();
  s.update(10, [1, 2, 3], [0, 0, 0], 10);
  assert.ok(near(s.at(10), [1, 2, 3]));
  assert.ok(near(s.at(12), [1, 2, 3]));
  assert.equal(s.settled(12), true);
});

test("target: a continuous ramp at 5 Hz is tracked without lag and without stutter", () => {
  const s = new TargetSmoother();
  const ramp = (t) => [[0, 0, 7 * t], [0, 0, 7]];
  const out = feed(s, 0, 6, 5, ramp);
  // after the first packet intervals the drawn point sits on the ramp (within a packet interval of motion, 1.4 m max)
  for (const [t, p] of out.filter(([t]) => t > 2.5)) assert.ok(Math.abs(p[2] - 7 * t) < 0.05, `t=${t} z=${p[2]}`);
  // per-frame displacement stays smooth: 7/60 m +- 5 mm
  for (let i = 1; i < out.length; i++) if (out[i][0] > 2.5) assert.ok(Math.abs(out[i][1][2] - out[i - 1][1][2] - 7 / 60) < 0.005);
});

test("target: velocity from the position difference when sp_vel is 0 but sp_pos moves", () => {
  const s = new TargetSmoother();
  const ramp = (t) => [[0, 0, 40 - 5 * t], [0, 0, 0]]; // like Deceleration: sp_vel reported as 0
  const out = feed(s, 0, 6, 5, ramp);
  for (const [t, p] of out.filter(([t]) => t > 2.5)) assert.ok(Math.abs(p[2] - (40 - 5 * t)) < 0.1, `t=${t} z=${p[2]}`);
});

test("target: no velocity is extrapolated for missing or invalid sp_vel", () => {
  for (const v of [null, undefined, [NaN, 0, 0], [1, 2], [Infinity, 0, 0]]) {
    const s = new TargetSmoother();
    s.update(0, [1, 1, 1], v, 0);
    assert.ok(near(s.at(0.4), [1, 1, 1]));
    assert.equal(s.settled(0.4), true);
  }
});

test("target: extrapolation stops after one packet interval plus margin (lost packets)", () => {
  const s = new TargetSmoother();
  feed(s, 0, 2, 5, (t) => [[7 * t, 0, 0], [7, 0, 0]]);
  const last = s.pos[0]; // newest packet position
  const far = s.at(10)[0];
  assert.ok(far - last < 7 * (1.25 * 0.2 + 0.05) + 1e-6 && far - last > 0.5 * 7 * 0.2, `held at ${far - last}`);
  assert.equal(s.settled(10), true);
});

test("target: a guidance step glides, never teleports, and ends on the new position", () => {
  const s = new TargetSmoother();
  const out = feed(s, 0, 8, 5, (t) => [t < 3 ? [0, 0, 120] : [0, 0, 0], [0, 0, 0]]);
  const after = out.filter(([t]) => t >= 3);
  assert.ok(after[0][1][2] > 100); // not snapped by the first frame after the step
  for (let i = 1; i < after.length; i++) assert.ok(after[i - 1][1][2] - after[i][1][2] < 120 * 0.2); // < 24 m per frame
  assert.ok(Math.abs(out[out.length - 1][1][2]) < 0.01);
  assert.ok(Math.abs(after[Math.round(0.25 * 60) * 3][1][2]) < 120 * 0.5); // most of the way after about 0.75 s
});

test("target: a step after a lost packet is not used as a velocity", () => {
  const s = new TargetSmoother();
  s.update(0, [0, 0, 0], [0, 0, 0], 0);
  s.update(0.05, [0, 0, 120], [0, 0, 0], 0.05); // 2400 m/s implied
  assert.ok(near(s.v, [0, 0, 0]));
});

test("target: invalid position gives null and clears", () => {
  const s = new TargetSmoother();
  s.update(0, [1, 1, 1], null, 0);
  s.update(1, [NaN, 0, 0], null, 1);
  assert.equal(s.at(1), null);
  s.update(2, null, null, 2);
  assert.equal(s.at(2), null);
});

test("target: repeated packet (same t) is ignored", () => {
  const s = new TargetSmoother();
  s.update(0, [0, 0, 0], [1, 0, 0], 0);
  s.update(0.3, [9, 9, 9], [1, 0, 0], 0);
  assert.ok(near(s.at(0.3), [0, 0, 0], 0.3)); // moving towards the extrapolation of the first packet only
});

test("target: time going backwards (replay seek) resets without gliding", () => {
  const s = new TargetSmoother();
  s.update(100, [5, 0, 0], null, 100);
  s.update(10, [1, 0, 0], null, 10);
  assert.ok(near(s.at(10), [1, 0, 0]));
  s.reset();
  assert.equal(s.at(10), null);
});

test("display settings: validation, zoom and pixel ratio", () => {
  assert.deepEqual(cleanDisplay({ ui: "1.5", res: "2" }), { ui: "1.5", res: "2" });
  assert.deepEqual(cleanDisplay({ ui: "9", res: "x" }), { ui: "auto", res: "auto" });
  assert.deepEqual(cleanDisplay(null), { ui: "auto", res: "auto" });
  assert.deepEqual(UI_SCALES, ["auto", "0.75", "1", "1.25", "1.5", "2"]);
  assert.equal(uiZoom("auto"), 1);
  assert.equal(uiZoom("1.25"), 1.25);
  assert.equal(pixelRatio(2, "auto", "auto"), 2);
  assert.equal(pixelRatio(3, "auto", "auto"), 3); // old cap of 2 is gone
  assert.equal(pixelRatio(1, "1.5", "2"), 3);
  assert.equal(pixelRatio(2, "2", "2"), MAX_PIXEL_RATIO);
  assert.equal(pixelRatio(1, "0.75", "1"), 0.75);
  assert.equal(pixelRatio(NaN, "auto", "1.5"), 1.5);
});
