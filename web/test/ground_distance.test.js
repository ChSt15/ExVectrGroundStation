import test from "node:test";
import assert from "node:assert/strict";
import { groundDistanceView } from "../app/status/ground_distance.js";
import { SIGNAL_BY_ID } from "../app/plot/catalogue.js";

const e = (gd, t = 10) => ({ type: "Actuators", t, f: { ground_distance: gd } });

test("ground distance: value with 2 decimals, NaN/null = no reading, stale and missing", () => {
  assert.deepEqual(groundDistanceView(e(1.234), 10), { text: "1.23", unit: "m", state: "ok" });
  assert.equal(groundDistanceView(e(NaN), 10).text, "no reading");
  assert.equal(groundDistanceView(e(null), 10).state, "none");
  assert.equal(groundDistanceView(e(undefined), 10).state, "none");
  assert.equal(groundDistanceView(e(2), 20).state, "stale");
  assert.equal(groundDistanceView(undefined, 10).state, "missing");
});

test("ground distance is a plottable signal (Actuators.ground_distance, m); NaN stays a gap", () => {
  const g = SIGNAL_BY_ID.get("act.ground_distance");
  assert.equal(g.label, "Ground distance");
  assert.equal(g.unit, "m");
  assert.equal(g.type, "Actuators");
  assert.equal(g.extract({ ground_distance: 1.5 }, {}), 1.5);
  assert.ok(Number.isNaN(g.extract({ ground_distance: null }, {})));
});
