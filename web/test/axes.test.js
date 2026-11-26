import test from "node:test";
import assert from "node:assert/strict";
import { axisRange, hasRight, manualRange, padsFor, seriesOn, sideOf } from "../app/plot/axes.js";
import { GRAPH_PRESETS, axisTitle, presetGraph, sanitizeGraphs, setAxis, toggleSignal } from "../app/plot/catalogue.js";
import { RingSeries, decimate } from "../app/plot/series.js";

test("axis assignment: default left, saved graphs without axis keep working", () => {
  assert.equal(sideOf({ sig: "a" }), "L");
  assert.equal(sideOf({ axis: "R" }), "R");
  assert.equal(sideOf({ axis: "x" }), "L");
  const out = sanitizeGraphs([{ title: "old", series: [{ sig: "att.roll", slot: 0, dash: false }, { sig: "att.pitch", slot: 1, axis: "R" }] }]);
  assert.deepEqual(out[0].series.map((x) => x.axis), ["L", "R"]);
  assert.equal(hasRight(out[0].series), true);
  assert.equal(hasRight(out[0].series.slice(0, 1)), false);
  assert.deepEqual(seriesOn(out[0].series, "R").map((x) => x.sig), ["att.pitch"]);
});

test("TVC preset has the thrust on the right axis with its own unit; setAxis / toggleSignal", () => {
  const g = presetGraph("tvc");
  assert.deepEqual(g.series.map((x) => [x.sig, x.axis]), [["act.tvc.x", "L"], ["act.tvc.y", "L"], ["act.thrust", "R"]]);
  assert.equal(axisTitle(g, "R"), "Thrust [N]");
  assert.equal(axisTitle(g, "L"), "deg");
  assert.equal(axisTitle({ series: [] }, "R"), "");
  const g2 = setAxis(g, "act.thrust", "L");
  assert.equal(hasRight(g2.series), false);
  assert.equal(setAxis(g2, "act.thrust", "R").series[2].axis, "R");
  assert.equal(toggleSignal(presetGraph("attitude"), "att.tilt").series.at(-1).axis, "L");
  for (const p of GRAPH_PRESETS) {
    for (const side of ["L", "R"]) {
      const units = new Set(presetGraph(p.id).series.filter((x) => x.axis === side).map((x) => x.sig));
      assert.ok(units.size <= 8);
    }
  }
});

test("each axis has its own auto range from its own series, a manual range wins", () => {
  const mk = (vals) => {
    const rs = new RingSeries(100);
    vals.forEach((v, i) => rs.push(i, v));
    return decimate(rs, 0, vals.length, 50).v;
  };
  const left = axisRange(null, [mk([-0.1, 0.1])]);
  const right = axisRange(null, [mk([0, 20])]);
  assert.ok(left[1] < 1 && right[1] > 20);
  assert.deepEqual(axisRange([2, 4], [mk([0, 20])]), [2, 4]);
  assert.deepEqual(axisRange([4, 2], [mk([0, 10])]).length, 2); // invalid manual: auto
  assert.deepEqual(manualRange(true, "1", "2"), null);
  assert.deepEqual(manualRange(false, "1", "2"), [1, 2]);
  assert.equal(manualRange(false, "3", "2"), null);
  assert.ok(padsFor(true).r > padsFor(false).r);
});
