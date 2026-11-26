import test from "node:test";
import assert from "node:assert/strict";
import { RingSeries, decimate } from "../app/plot/series.js";
import { MIN_SPAN_S, ZoomStack, boxToRanges, dragAction, panWindow, panY, zoomAbout, zoomTimeRange, zoomYRange } from "../app/plot/zoom.js";
import { MissionClock } from "../app/status/mission_clock.js";
import { fmtT, missionAxis } from "../app/plot/missionclock.js";

const rect = { left: 50, top: 10, width: 400, height: 200 };
const view = { t0: 100, t1: 140, y0: -1, y1: 3 };
const near = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} vs ${b}`);

test("box to ranges: time and y from pixels, order independent", () => {
  const r = boxToRanges({ x0: 250, y0: 110, x1: 150, y1: 10 }, rect, view);
  near(r.t[0], 110);
  near(r.t[1], 120);
  near(r.y[0], 1);
  near(r.y[1], 3);
});

test("box: tiny drag does nothing, thin box zooms one axis, modifiers pick the axis", () => {
  assert.equal(boxToRanges({ x0: 100, y0: 50, x1: 102, y1: 52 }, rect, view), null);
  const thin = boxToRanges({ x0: 100, y0: 50, x1: 200, y1: 51 }, rect, view);
  assert.ok(thin.t && thin.y === null);
  const tOnly = boxToRanges({ x0: 100, y0: 50, x1: 200, y1: 150 }, rect, view, { mode: "time" });
  assert.ok(tOnly.t && tOnly.y === null);
  const yOnly = boxToRanges({ x0: 100, y0: 50, x1: 200, y1: 150 }, rect, view, { mode: "y" });
  assert.ok(yOnly.y && yOnly.t === null);
  assert.equal(boxToRanges({ x0: 100, y0: 50, x1: 102, y1: 150 }, rect, view, { mode: "time" }), null);
});

test("box outside the plot area is clamped; time clamped to the loaded data", () => {
  const r = boxToRanges({ x0: -100, y0: -50, x1: 9999, y1: 9999 }, rect, view, { minT: 105, maxT: 135 });
  assert.deepEqual(r.t, [105, 135]);
  near(r.y[0], -1);
  near(r.y[1], 3);
  assert.equal(boxToRanges({ x0: 50, y0: 50, x1: 100, y1: 52 }, rect, view, { minT: 120, maxT: 130, mode: "time" }), null);
});

test("minimum spans", () => {
  const [a, b] = zoomTimeRange(120, 120.001, 0, 1000);
  near(b - a, MIN_SPAN_S);
  const [c, d] = zoomTimeRange(999.999, 1000, 0, 1000);
  assert.ok(d <= 1000 && near(d - c, MIN_SPAN_S) === undefined);
  const [lo, hi] = zoomYRange(5, 5);
  assert.ok(hi > lo);
  assert.deepEqual(zoomYRange(3, 1), [1, 3]);
});

test("pan window: span kept, never past now, not before the oldest data", () => {
  assert.deepEqual(panWindow(100, 110, 5, 0, 112), [102, 112]);
  assert.deepEqual(panWindow(100, 110, -5, 98, 200), [98, 108]);
  assert.deepEqual(panWindow(90, 100, -5, 98, 200), [90, 100]); // already before the data: no further
  assert.deepEqual(panWindow(100, 110, -5, 0, 200), [95, 105]);
  assert.deepEqual(panY(1, 3, 0.5), [1.5, 3.5]);
});

test("wheel zoom about a point keeps that point fixed", () => {
  const [a, b] = zoomAbout(100, 140, 0.25, 0.5);
  near(a + 0.25 * (b - a), 110);
  near(b - a, 20);
  const [c, d] = zoomAbout(100, 140, 1, 0.5);
  assert.deepEqual([c, d], [120, 140]);
  const [e, f] = zoomAbout(100, 140, 0.9, 2, { max: 141, maxSpan: 60 });
  near(f - e, 60);
  assert.ok(f <= 141 + 1e-9);
  const [g, h] = zoomAbout(0, 1, 0.5, 1e-9);
  near(h - g, MIN_SPAN_S);
});

test("zoom stack", () => {
  const z = new ZoomStack(3);
  for (let i = 0; i < 5; i++) z.push(i);
  assert.equal(z.length, 3);
  assert.equal(z.pop(), 4);
  z.clear();
  assert.equal(z.pop(), null);
});

test("zoomed in below the sample period the raw samples are drawn, and the points per column stay bounded", () => {
  const rs = new RingSeries(20000);
  for (let i = 0; i < 12000; i++) rs.push(i * 0.01, Math.sin(i));
  const tiny = decimate(rs, 50.0, 50.02, 600);
  assert.ok(tiny.t.length <= 5);
  const wide = decimate(rs, 0, 120, 600);
  assert.ok(wide.t.length <= 1200 + 4);
  const mid = decimate(rs, 10, 11, 600);
  assert.ok(mid.t.length <= 1200 + 4);
});

test("mission axis: ticks in mission seconds, labels T-/T+", () => {
  const c = new MissionClock();
  for (let i = 0; i <= 100; i++) c.add(500 + i, -30 + i); // T0 at 530
  const ax = missionAxis(c, 520, 540, 5);
  assert.equal(ax.mode, "mission");
  assert.ok(ax.ticks.length >= 3);
  const zero = ax.ticks.find((k) => k.label === "T0");
  assert.ok(zero);
  near(zero.t, 530);
  assert.ok(ax.ticks.some((k) => k.label.startsWith("T-")) && ax.ticks.some((k) => k.label.startsWith("T+")));
  assert.equal(ax.label(535), "T+5.00");
  assert.match(ax.title, /mission time/);
});

test("mission axis: window with a mission change still labels each tick at its own time", () => {
  const c = new MissionClock();
  for (let i = 0; i <= 10; i++) c.add(100 + i, -10 + i); // T0 at 110
  for (let i = 0; i <= 10; i++) c.add(200 + i, -10 + i); // T0 at 210
  const ax = missionAxis(c, 90, 220, 8);
  assert.equal(ax.mode, "mission");
  assert.ok(ax.ticks.every((k) => k.t >= 90 && k.t <= 220));
});

test("mission axis: no clock falls back to relative; replay window without clock too", () => {
  const c = new MissionClock();
  assert.equal(missionAxis(c, 0, 10).mode, "relative");
  for (let i = 0; i < 5; i++) c.add(1000 + i, -2 + i, i < 2 ? "Idle" : "Hover");
  assert.equal(missionAxis(c, 0, 10).mode, "relative"); // the window ends before the mission started
  c.add(1005, null, "Completed");
  c.add(1012, null, "Idle");
  assert.equal(missionAxis(c, 5000, 5010).mode, "relative"); // long after the end
  assert.equal(missionAxis(c, 998, 1010).mode, "mission");
});

test("fmtT", () => {
  assert.equal(fmtT(-30), "T-30");
  assert.equal(fmtT(12.5, 1), "T+12.5");
  assert.equal(fmtT(0), "T0");
  assert.equal(fmtT(-0.001, 1), "T0");
  assert.equal(fmtT(NaN), "—");
});

test("mouse mapping: left drag pans, right drag draws the zoom box", () => {
  assert.equal(dragAction(0), "pan");
  assert.equal(dragAction(2), "box");
  assert.equal(dragAction(1), null);
});

test("box zoom also maps the same pixel box on a right y axis", () => {
  const r = boxToRanges({ x0: 100, y0: 10, x1: 200, y1: 60 }, rect, { ...view, r0: 0, r1: 1000 });
  assert.ok(r.y && r.yr && r.yr[1] > r.yr[0]);
  assert.ok(r.yr[1] - r.yr[0] < 1000);
  const t = boxToRanges({ x0: 100, y0: 10, x1: 200, y1: 60 }, rect, { ...view, r0: 0, r1: 1000 }, { mode: "time" });
  assert.equal(t.yr, null);
  assert.equal("yr" in boxToRanges({ x0: 100, y0: 10, x1: 200, y1: 60 }, rect, view), false);
});

test("missionAxis: a window with two missions uses the clock of the mission that fills most of it", () => {
  const c = new MissionClock();
  for (let i = 0; i < 20; i++) c.add(100 + i, -10 + i, "Hover", "Running"); // T0 = 110
  c.add(120, 9, "Completed", "Finished");
  for (let i = 0; i < 40; i++) c.add(200 + i, -10 + i, "Hover", "Startup"); // mission 2, T0 = 210
  assert.equal(c.missions.length, 2);
  const ax = missionAxis(c, 150, 250, 5);
  assert.equal(ax.mode, "mission");
  assert.equal(ax.label(210), "T0");
  assert.equal(ax.label(215, 0), "T+5");
  assert.ok(ax.ticks.length > 0 && ax.ticks.every((k) => k.t >= 150 && k.t <= 250));
  const k0 = ax.ticks.find((k) => k.label === "T0");
  assert.ok(k0 && Math.abs(k0.t - 210) < 1e-9, "T0 tick sits at mission 2's T0");
});
