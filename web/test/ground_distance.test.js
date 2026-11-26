import test from "node:test";
import assert from "node:assert/strict";
import { groundDistanceView } from "../app/status/ground_distance.js";
import { SIGNAL_BY_ID, sanitizeGraphs } from "../app/plot/catalogue.js";

const e = (gd, t = 10) => ({ type: "Position", t, f: { ground_distance: gd } });

test("ground distance: value with 2 decimals, NaN/null = no reading, stale and missing", () => {
  assert.deepEqual(groundDistanceView(e(1.234), 10), { text: "1.23", unit: "m", state: "ok" });
  assert.equal(groundDistanceView(e(NaN), 10).text, "no reading");
  assert.equal(groundDistanceView(e(null), 10).state, "none");
  assert.equal(groundDistanceView(e(undefined), 10).state, "none");
  assert.equal(groundDistanceView(e(2), 20).state, "stale");
  assert.equal(groundDistanceView(undefined, 10).state, "missing");
});

test("ground distance is a plottable signal (Position.ground_distance, m); NaN stays a gap", () => {
  const g = SIGNAL_BY_ID.get("pos.ground_distance");
  assert.equal(g.label, "Ground distance");
  assert.equal(g.unit, "m");
  assert.equal(g.type, "Position");
  assert.equal(g.extract({ ground_distance: 1.5 }, {}), 1.5);
  assert.ok(Number.isNaN(g.extract({ ground_distance: null }, {})));
});

test("a saved graph with the old act.ground_distance id resolves to pos.ground_distance", () => {
  const out = sanitizeGraphs([{ title: "g", series: [{ sig: "act.ground_distance", slot: 2, dash: true }, { sig: "nonsense" }] }]);
  assert.deepEqual(out[0].series, [{ sig: "pos.ground_distance", slot: 2, dash: true }]);
});
