import test from "node:test";
import assert from "node:assert/strict";
import { Trail } from "../app/view3d/trail.js";
import {
  DEFAULT_FLAP_DEG, validateModel, flapRotationRad, resolveFlapAngles, flapOutline, polygonArea, normalizeTransform, flapCant,
} from "../app/view3d/model_config.js";

test("trail: live samples are added, thinned and pruned to the window", () => {
  const tr = new Trail(10, { minDt: 0.1 });
  assert.equal(tr.add(0, [0, 0, 0]), "added");
  assert.equal(tr.add(0.05, [1, 0, 0]), "skipped");
  for (let t = 0.2; t <= 20; t += 0.2) tr.add(t, [t, 0, 0]);
  tr.prune(20);
  assert.ok(tr.pts[0].t >= 10 - 1e-9);
  const f = tr.fades(20);
  assert.ok(f[f.length - 1] > 0.99 && f[0] < 0.05);
});

test("trail: a sample older than the last one (replay seek back) resets it", () => {
  const tr = new Trail(60);
  tr.add(100, [1, 1, 1]);
  tr.add(101, [2, 2, 2]);
  assert.equal(tr.add(50, [0, 0, 0]), "reset");
  assert.equal(tr.pts.length, 1);
});

test("trail: load from history skips null entries and sorts by time", () => {
  const tr = new Trail(60);
  tr.load({ t: [3, 1, 2], pos: [[3, 0, 0], [1, 0, 0], null] });
  assert.deepEqual(tr.pts.map((p) => p.t), [1, 3]);
});

const flap = (over = {}) => ({ id: "tl", index: 0, source: { primitive: "flap" }, pivot_body_m: [0, 0.065, 0.23], axis_body: [0, 0, 1], sign: -1, model_angle_deg: 0, ...over });

test("flap rotation = sign * (angle - model angle)", () => {
  assert.ok(Math.abs(flapRotationRad(flap(), Math.PI / 2) + Math.PI / 2) < 1e-12);
  assert.ok(Math.abs(flapRotationRad(flap({ sign: 1, model_angle_deg: 90 }), Math.PI / 4) + Math.PI / 4) < 1e-12);
  assert.equal(flapRotationRad(flap({ model_angle_deg: 30 }), (30 * Math.PI) / 180), -0);
});

test("flaps show 45 degrees without FlapState and follow it with it", () => {
  const cfg = { default_flap_angle_deg: 45 };
  const r = resolveFlapAngles(cfg, undefined);
  assert.equal(r.source, "default");
  assert.ok(r.angles.every((a) => Math.abs(a - Math.PI / 4) < 1e-12));
  assert.equal(DEFAULT_FLAP_DEG, 45);
  const d = resolveFlapAngles(cfg, { f: { angle: [0, 0.5, 1, 1.5] } });
  assert.equal(d.source, "data");
  assert.deepEqual(d.angles, [0, 0.5, 1, 1.5]);
  // a packet without the field (reduced radio set) counts as no data
  assert.equal(resolveFlapAngles(cfg, { f: {} }).source, "default");
  // individual non-finite elements fall back to the default
  assert.ok(Math.abs(resolveFlapAngles(cfg, { f: { angle: [0, null, 1, 1] } }).angles[1] - Math.PI / 4) < 1e-12);
});

test("validateModel reports problems and accepts a good config", () => {
  assert.deepEqual(validateModel({ body: { source: { primitive: "x" } }, flaps: [flap()] }), []);
  assert.ok(validateModel({}).length >= 2);
  const bad = validateModel({ body: { source: { primitive: "x" } }, flaps: [flap({ sign: 2, axis_body: [0, 0, 0], index: 7 })] });
  assert.equal(bad.length, 3);
  assert.deepEqual(normalizeTransform(undefined), { rotation_deg_xyz: [0, 0, 0], translation_m: [0, 0, 0], scale: 1 });
});

test("flap outline: chamfered plate has the right extent and area", () => {
  const top = flapOutline({ width_m: 0.07, height_m: 0.14, chamfer_m: 0.08, chamfer_corner: "out_top" });
  const c = 0.08 / Math.SQRT2; // 5.66 cm legs, below both width and height
  assert.ok(Math.abs(polygonArea(top) - (0.07 * 0.14 - (c * c) / 2)) < 1e-9);
  assert.equal(Math.max(...top.map((p) => p[0])), 0.07);
  assert.ok(Math.abs(Math.max(...top.map((p) => p[1])) - 0.07) < 1e-12);
  const bot = flapOutline({ width_m: 0.08, height_m: 0.21, chamfer_m: 0.08, chamfer_corner: "out_bottom" });
  assert.ok(Math.abs(polygonArea(bot) - (0.08 * 0.21 - (c * c) / 2)) < 1e-9);
  // the cut is at the bottom outer corner: the outer edge starts above the lower edge
  assert.ok(bot.some((p) => p[0] === 0.08 && p[1] > -0.105 && p[1] < -0.04));
  assert.equal(flapOutline({ width_m: 1, height_m: 1 }).length, 4);
});

test("out_top puts the chamfer at the top (nose side, +z) of the outer edge, out_bottom at the bottom", () => {
  const maxZAtOuter = (pts, w) => Math.max(...pts.filter(([x]) => x === w).map(([, z]) => z));
  const minZAtOuter = (pts, w) => Math.min(...pts.filter(([x]) => x === w).map(([, z]) => z));
  const top = flapOutline({ width_m: 0.08, height_m: 0.21, chamfer_m: 0.08, chamfer_corner: "out_top" });
  assert.ok(maxZAtOuter(top, 0.08) < 0.105 - 0.04); // the top of the outer edge is cut away
  assert.equal(minZAtOuter(top, 0.08), -0.105); // the bottom corner is square
  const bot = flapOutline({ width_m: 0.08, height_m: 0.21, chamfer_m: 0.08, chamfer_corner: "out_bottom" });
  assert.equal(maxZAtOuter(bot, 0.08), 0.105);
  assert.ok(minZAtOuter(bot, 0.08) > -0.105 + 0.04);
});

test("flap cant: lower hinge point unchanged, upper end moves toward the axis by length*sin(cant)", () => {
  const top = { pivot_body_m: [0, 0.065, 0.23], source: { height_m: 0.14 }, cant_deg: 10 };
  const rot = (c, y, z, a) => [y * Math.cos(a) - z * Math.sin(a), y * Math.sin(a) + z * Math.cos(a)];
  for (const side of [1, -1]) {
    const f = { ...top, pivot_body_m: [0, side * 0.065, 0.23] };
    const { point, angleRad } = flapCant(f);
    assert.deepEqual(point.map((v) => +v.toFixed(6)), [0, side * 0.065, 0.16]);
    const [y, z] = rot(0, 0, 0.14, angleRad); // upper end relative to the lower hinge point
    assert.ok(Math.abs(y + side * 0.14 * Math.sin(10 * Math.PI / 180)) < 1e-12, "upper end moves toward the axis");
    assert.ok(Math.abs(side * 0.065 + y) < Math.abs(side * 0.065));
    assert.ok(Math.abs(z - 0.14 * Math.cos(10 * Math.PI / 180)) < 1e-12);
  }
  assert.equal(flapCant({ pivot_body_m: [0, 0.065, -0.255], source: { height_m: 0.21 } }).angleRad, 0);
});
