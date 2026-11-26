import test from "node:test";
import assert from "node:assert/strict";
import { LayerCache, layerKey } from "../app/plot/layercache.js";

const rs = { length: 100, lastT: 99, firstT: 0 };
const base = () => ({
  w: 600, h: 200, dpr: 1, t0: 0, t1: 100, yRange: null, yRangeR: null,
  series: [{ id: "att.roll", slot: 0, dash: false, axis: "L", rs }],
  axes: null, xTicks: [{ t: 0, label: "0" }], xTitle: "t", colors: "#fff|#000",
});
const key = (patch = {}) => layerKey({ ...base(), ...patch });

test("the key does not depend on the cursor or the bar (not part of the layer)", () => {
  assert.equal(key(), key());
  assert.equal(key({ cursorT: 5, hoverT: 6 }), key());
});

test("size, dpr, window, manual y range, theme, axes, ticks and title invalidate", () => {
  const k = key();
  for (const patch of [
    { w: 601 }, { h: 201 }, { dpr: 2 }, { t0: 1 }, { t1: 99 }, { yRange: [0, 1] }, { yRangeR: [0, 1] }, { colors: "#000|#fff" },
    { axes: { L: { label: "a" }, R: { label: "b" } } }, { xTicks: [{ t: 0, label: "1" }] }, { xTitle: "x" },
  ]) assert.notEqual(key(patch), k, JSON.stringify(patch));
});

test("data changes (new samples, another signal, slot, axis, dash) invalidate", () => {
  const k = key();
  const s = (patch) => [{ id: "att.roll", slot: 0, dash: false, axis: "L", rs, ...patch }];
  assert.notEqual(key({ series: s({ rs: { ...rs, length: 101, lastT: 100 } }) }), k);
  assert.notEqual(key({ series: s({ id: "att.pitch" }) }), k);
  assert.notEqual(key({ series: s({ slot: 1 }) }), k);
  assert.notEqual(key({ series: s({ axis: "R" }) }), k);
  assert.notEqual(key({ series: s({ dash: true }) }), k);
});

test("LayerCache is valid only for the stored key", () => {
  const c = new LayerCache();
  assert.equal(c.valid(key()), false);
  c.store(key(), { n: 1 });
  assert.equal(c.valid(key()), true);
  assert.equal(c.valid(key({ w: 5 })), false);
  c.invalidate();
  assert.equal(c.valid(key()), false);
});
