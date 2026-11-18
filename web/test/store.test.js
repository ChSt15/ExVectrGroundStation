import test from "node:test";
import assert from "node:assert/strict";
import { Store } from "../app/store.js";

const pkt = (type, src, t, f = {}, extra = {}) => ({ type, src, t, f, ...extra });

test("snapshot replaces the view, packets merge, per (type, src)", () => {
  let perf = 10;
  const s = new Store(() => perf);
  s.applySnapshot("live", { clock: 100, items: [pkt("Position", "usb", 99, { pos: [1, 2, 3] }, { iv: 0.1, stale_after: 0.5 })] });
  assert.equal(s.get("live", "Position", "usb").f.pos[0], 1);
  s.applyPackets("live", { clock: 101, items: [pkt("Position", "link", 100.5, { pos: [4, 5, 6] }), pkt("Attitude", "usb", 100.9, {})] });
  assert.equal(s.all("live", "Position").length, 2);
  assert.equal(s.get("live", "Position").src, "link", "freshest source wins without src");
  s.applySnapshot("live", { clock: 102, items: [] });
  assert.equal(s.all("live").length, 0);
});

test("all-mode packets keep the last known iv and stale_after", () => {
  const s = new Store(() => 0);
  s.applySnapshot("live", { clock: 1, items: [pkt("A", "usb", 1, {}, { iv: 0.2, stale_after: 0.9 })] });
  s.applyPackets("live", { clock: 2, items: [pkt("A", "usb", 2, {})] });
  assert.equal(s.get("live", "A", "usb").stale_after, 0.9);
});

test("view clock follows the core clock, not the browser wall clock, and respects the rate", () => {
  let perf = 10;
  const s = new Store(() => perf);
  assert.equal(s.now("live"), null);
  s.setClock("live", 1000);
  perf = 12.5;
  assert.equal(s.now("live"), 1002.5);
  s.setClock("replay", 50, 0); // paused replay: clock frozen
  perf = 20;
  assert.equal(s.now("replay"), 50);
  s.setClock("replay", 50, 2); // playing at 2x
  perf = 21;
  assert.equal(s.now("replay"), 52);
  s.setRate("replay", 0);
  perf = 30;
  assert.equal(s.now("replay"), 52);
});

test("views are independent and clear keeps the clock", () => {
  const s = new Store(() => 0);
  s.applySnapshot("replay", { clock: 5, items: [pkt("A", "usb", 5)] });
  assert.equal(s.all("live").length, 0);
  s.clear("replay");
  assert.equal(s.all("replay").length, 0);
  assert.equal(s.now("replay"), 5);
});

test("change listeners get snapshot and per-packet events", () => {
  const s = new Store(() => 0);
  const seen = [];
  const off = s.onChange((kind, view, item) => seen.push([kind, view, item && item.type]));
  s.applySnapshot("live", { clock: 1, items: [pkt("A", "usb", 1)] });
  s.applyPackets("live", { clock: 2, items: [pkt("B", "usb", 2)] });
  off();
  s.applyPackets("live", { clock: 3, items: [pkt("C", "usb", 3)] });
  assert.deepEqual(seen, [["snapshot", "live", undefined], ["packet", "live", "A"], ["packet", "live", "B"]]);
});
