import test from "node:test";
import assert from "node:assert/strict";
import { RingSeries, decimate, niceTicks, paddedRange, tickDigits, toCsv, valueRange, viewWindow } from "../app/plot/series.js";
import {
  DEFAULT_GRAPHS, GRAPH_PRESETS, MAX_SERIES, SIGNALS, SIGNAL_BY_ID, SIGNAL_TYPES, SignalFeeder, graphUnits, historyToPackets,
  presetGraph, sanitizeGraphs, toggleSignal,
} from "../app/plot/catalogue.js";

test("ring buffer keeps the newest samples in order and drops out-of-order ones", () => {
  const r = new RingSeries(4);
  for (let i = 0; i < 6; i++) r.push(i, i * 10);
  assert.equal(r.length, 4);
  assert.deepEqual([0, 1, 2, 3].map((i) => r.tAt(i)), [2, 3, 4, 5]);
  assert.equal(r.firstT, 2);
  assert.equal(r.lastT, 5);
  assert.equal(r.push(4.5, 1), false);
  assert.equal(r.lowerBound(3.5), 2);
  assert.deepEqual(r.nearest(3.4), { t: 3, v: 30 });
  assert.equal(r.nearest(100, 1), null);
  r.push(6, null);
  assert.ok(Number.isNaN(r.vAt(3)));
});

test("decimation: 100 Hz for 2 min on 600 columns draws at most 2 points per column and keeps spikes", () => {
  const r = new RingSeries(16384);
  for (let i = 0; i < 12000; i++) r.push(i / 100, i === 6123 ? 50 : Math.sin(i / 100));
  const d = decimate(r, 0, 120, 600);
  assert.ok(d.t.length <= 1200 + 4, `${d.t.length} points`);
  assert.ok(d.v.includes(50), "spike kept");
  for (let i = 1; i < d.t.length; i++) assert.ok(d.t[i] >= d.t[i - 1]);
  const t0 = performance.now();
  for (let k = 0; k < 20; k++) decimate(r, 0, 120, 600);
  assert.ok((performance.now() - t0) / 20 < 20, "fast enough for 10 graphs at 10 Hz");
});

test("decimation: small windows pass through, gaps and NaN break the line, edges included", () => {
  const r = new RingSeries(100);
  [0, 0.1, 0.2, 2.0, 2.1].forEach((t) => r.push(t, t));
  r.push(2.2, NaN);
  r.push(2.3, 1);
  const d = decimate(r, 0.15, 2.25, 100, 0.5);
  assert.equal(d.t[0], 0.1); // one sample before the window
  const nanAt = d.v.findIndex((v) => Number.isNaN(v));
  assert.equal(d.t[nanAt], 0.2); // time gap 0.2 -> 2.0 becomes a break
  assert.ok(d.v.slice(nanAt + 1).some((v) => Number.isNaN(v))); // the NaN sample too
  assert.deepEqual(decimate(new RingSeries(4), 0, 1, 10), { t: [], v: [] });
});

test("ranges, ticks, window and CSV", () => {
  assert.deepEqual(valueRange([[1, NaN, 3], [-2]]), [-2, 3]);
  assert.equal(valueRange([[NaN]]), null);
  assert.deepEqual(paddedRange([0, 10], 0.1), [-1, 11]);
  const flat = paddedRange([5, 5]);
  assert.ok(flat[0] < 5 && flat[1] > 5);
  assert.deepEqual(niceTicks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
  assert.deepEqual(niceTicks(-0.3, 0.3, 3), [-0.2, 0, 0.2]);
  assert.ok(niceTicks(-9, 9, 3).length >= 3, "a symmetric range gets ticks on both sides");
  assert.equal(tickDigits([0, 0.2, 0.4]), 1);
  assert.equal(tickDigits([0, 20, 40]), 0);
  assert.deepEqual(viewWindow({ now: 100, windowS: 10, follow: true }), [90, 100]);
  assert.deepEqual(viewWindow({ now: 100, windowS: 10, follow: false, endT: 50 }), [40, 50]);
  assert.deepEqual(viewWindow({ now: 100, windowS: 10, follow: false, endT: 500 }), [90, 100]);
  assert.deepEqual(viewWindow({ now: 100, windowS: Infinity, follow: true, firstT: 20 }), [20, 100]);
  const a = new RingSeries(10);
  const b = new RingSeries(10);
  a.push(1, 1.5);
  a.push(2, NaN);
  b.push(2, 7);
  assert.equal(toCsv([{ name: "a [deg]", rs: a }, { name: 'b "x"', rs: b }], 0, 10), 't_s,a [deg],"b ""x"""\n1.000000,1.5,\n2.000000,NaN,7\n');
});

test("signal catalogue: unique ids, units, presets use known signals, one unit per preset", () => {
  assert.equal(new Set(SIGNALS.map((s) => s.id)).size, SIGNALS.length);
  for (const s of SIGNALS) assert.ok(s.unit && s.type && s.label, s.id);
  for (const id of ["Attitude", "Position", "MissionState", "Actuators", "FlapState", "ImuRaw", "Estimator", "TaskTiming", "Calibration"]) assert.ok(SIGNAL_TYPES.includes(id), id);
  for (const g of GRAPH_PRESETS) {
    assert.ok(g.series.length <= MAX_SERIES, g.id);
    for (const [id] of g.series) assert.ok(SIGNAL_BY_ID.has(id), `${g.id}: ${id}`);
    assert.equal(graphUnits(presetGraph(g.id)).length, 1, `${g.id} mixes units`);
  }
  for (const id of DEFAULT_GRAPHS) assert.ok(presetGraph(id), id);
  assert.equal(presetGraph("nope"), null);
});

test("feeder: SI packets become display units, derived signals use the latest other packet, unsupplied fields are skipped", () => {
  const f = new SignalFeeder(() => new RingSeries(100));
  f.push("Attitude", 1, { q: [1, 0, 0, 0], omega: [Math.PI, 0, null] });
  assert.equal(f.ring("rate.x").vAt(0), 180);
  assert.ok(Number.isNaN(f.ring("rate.z").vAt(0))); // NaN on the wire is a gap
  assert.equal(f.ring("att.roll").vAt(0), 0);
  f.push("Position", 1, { pos: [1, 2, 3], vel: [0, 0, 1] });
  f.push("MissionState", 1.05, { sp_pos: [1, 2, 5], sp_vel: [0, 0, 0] });
  assert.equal(f.ring("err.pos.U").vAt(0), 2); // target minus current
  assert.equal(f.ring("err.vel.U").vAt(0), -1);
  f.push("FlapState", 1, { angle: [0, Math.PI / 2, 0, 0] }); // the vehicle sends no cmd
  assert.equal(f.ring("flap.TR").vAt(0), 90);
  assert.ok(!f.supplied.has("flap.cmd.TL"));
  f.push("ImuRaw", 1, { gyro: [0.1, 0, 0], accel: [0, 0, 9.81], mag: [2e-5, 0, 0] });
  assert.ok(!f.supplied.has("cal.gyro.x")); // no CALIBRATION yet: nothing is guessed
  const eye = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  f.push("Calibration", 1.5, { raw_frame: 0, gyro_offset: [0.1, 0, 0], gyro_matrix: eye, accel_offset: [0, 0, 0], accel_matrix: eye, mag_offset: [0, 0, 0], mag_matrix: eye, gyro_bias_est: [0, 0, 0] });
  f.push("ImuRaw", 2, { gyro: [0.1, 0, 0], accel: [0, 0, 9.81], mag: [2e-5, 0, 0] });
  assert.equal(f.ring("cal.gyro.x").vAt(0), 0);
  assert.ok(Math.abs(f.ring("imu.mag.x").vAt(1) - 20) < 1e-9); // T -> µT
  f.push("TaskTiming", 2, { avg: [1e-4, null], max: [] });
  assert.ok(Math.abs(f.ring("timing.avg.0").vAt(0) - 0.1) < 1e-12); // s -> ms
  f.clear();
  assert.equal(f.ring("rate.x").length, 0);
});

test("graph editing and stored graphs", () => {
  const g = presetGraph("attitude");
  const g2 = toggleSignal(g, "att.tilt");
  assert.equal(g2.series.length, 4);
  assert.equal(g2.series[3].slot, 3);
  assert.equal(toggleSignal(g2, "att.roll").series.length, 3);
  assert.equal(toggleSignal(g, "bogus"), g);
  const stored = sanitizeGraphs([{ title: "x", series: [{ sig: "att.roll", slot: 9 }, { sig: "gone" }] }, null, 3]);
  assert.deepEqual(stored, [{ preset: "", title: "x", note: "", series: [{ sig: "att.roll", slot: 1, dash: false }] }]);
  assert.equal(sanitizeGraphs("x"), null);
});

test("history replies become time-ordered packets", () => {
  const p = historyToPackets([
    { type: "Position", src: null, t: [1, 3], pos: [[0, 0, 0], [1, 1, 1]] },
    { type: "MissionState", src: null, t: [2], sp_pos: [[0, 0, 1]] },
  ]);
  assert.deepEqual(p.map((x) => [x.type, x.t]), [["Position", 1], ["MissionState", 2], ["Position", 3]]);
  assert.deepEqual(p[1].f, { sp_pos: [0, 0, 1] });
});
