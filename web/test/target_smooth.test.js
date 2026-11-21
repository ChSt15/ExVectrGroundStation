import test from "node:test";
import assert from "node:assert/strict";
import { TargetSmoother } from "../app/view3d/target_smooth.js";
import { UI_SCALES, cleanDisplay, uiZoom, pixelRatio, MAX_PIXEL_RATIO } from "../app/display_settings.js";

const near = (a, b, e = 1e-9) => a.every((x, i) => Math.abs(x - b[i]) < e);

test("target: extrapolates with sp_vel, holds after 0.5 s", () => {
  const s = new TargetSmoother();
  s.update(10, [1, 2, 3], [2, 0, -1], 10);
  assert.ok(near(s.at(10), [1, 2, 3]));
  assert.ok(near(s.at(10.2), [1.4, 2, 2.8]));
  assert.ok(near(s.at(11), [2, 2, 2.5])); // clamped at 0.5 s
  assert.ok(near(s.at(50), [2, 2, 2.5]));
  assert.equal(s.settled(11), true);
  assert.equal(s.settled(10.2), false);
});

test("target: no extrapolation without usable velocity", () => {
  for (const v of [null, undefined, [NaN, 0, 0], [1, 2], [Infinity, 0, 0]]) {
    const s = new TargetSmoother();
    s.update(0, [1, 1, 1], v, 0);
    assert.ok(near(s.at(0.4), [1, 1, 1]));
    assert.equal(s.settled(0.4), true);
  }
});

test("target: invalid position gives null and clears", () => {
  const s = new TargetSmoother();
  s.update(0, [1, 1, 1], null, 0);
  s.update(1, [NaN, 0, 0], null, 1);
  assert.equal(s.at(1), null);
  s.update(2, null, null, 2);
  assert.equal(s.at(2), null);
});

test("target: a new packet blends instead of snapping, then converges", () => {
  const s = new TargetSmoother({ tauS: 0.15 });
  s.update(0, [0, 0, 0], [1, 0, 0], 0);
  const before = s.at(0.2); // 0.2 shown
  s.update(0.2, [0.5, 0, 0], [1, 0, 0], 0.2); // raw jumps to 0.5
  assert.ok(near(s.at(0.2), before)); // continuous at the switch
  const later = s.at(0.2 + 0.15); // one time constant, vel extrapolation 0.15
  const raw = 0.5 + 0.15;
  assert.ok(Math.abs(later[0] - (raw + (before[0] - 0.5) * Math.exp(-1))) < 1e-9);
  assert.ok(Math.abs(s.at(0.2 + 3)[0] - 1.0) < 1e-3); // converged to raw (held at 0.5 s extrap)
  assert.equal(s.settled(3.2), true);
});

test("target: repeated packet (same t) is ignored", () => {
  const s = new TargetSmoother();
  s.update(0, [0, 0, 0], [1, 0, 0], 0);
  s.update(0.3, [0, 0, 0], [1, 0, 0], 0);
  assert.ok(near(s.at(0.3), [0.3, 0, 0]));
});

test("target: time going backwards (replay seek) resets without blending", () => {
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
