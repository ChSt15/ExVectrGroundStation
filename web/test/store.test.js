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

// ---- field-level merge over sources (no flicker between a full USB set and a reduced radio set)

import { describe as describeValue } from "../app/fmt.js";

const usbAct = (t) => pkt("Actuators", "usb", t, { thrust: 0, ground_distance: 0.5 }, { iv: 0.02, stale_after: 0.5 });
const linkAct = (t) => pkt("Actuators", "link", t, { thrust: 0, roll_torque: 0.1 }, { iv: 0.04, stale_after: 0.5 });

test("merge: a field only one source carries is steady while the sources alternate", () => {
  const s = new Store(() => 0);
  s.applySnapshot("live", { clock: 10, priority: ["usb", "link"], items: [usbAct(9.99), linkAct(9.98)] });
  const schemaOf = () => undefined;
  const look = (t, src) => s.get("live", t, src);
  for (let i = 0; i < 20; i++) {
    const t = 10 + i * 0.01;
    s.applyPackets("live", { clock: t, items: [i % 2 ? linkAct(t) : usbAct(t)] }); // alternate USB / radio packets
    const rt = describeValue({ type: "Actuators", field: "roll_torque", digits: 2 }, look, s.now("live"), schemaOf);
    const gd = describeValue({ type: "Actuators", field: "ground_distance", digits: 2 }, look, s.now("live"), schemaOf);
    assert.equal(rt.text, "0.10", `roll_torque at step ${i}`);
    assert.equal(gd.text, "0.50", `ground_distance at step ${i}`);
    assert.equal(rt.src, "link");
    assert.equal(gd.src, "usb");
    assert.equal(rt.state, "fresh");
  }
});

test("merge: priority picks the field source, per field age and staleness", () => {
  const s = new Store(() => 0);
  s.applySnapshot("live", {
    clock: 10,
    priority: ["usb", "link"],
    items: [
      pkt("Mission", "usb", 10, { phase: "A" }, { stale_after: 0.5 }),
      pkt("Mission", "link", 9.9, { phase: "B", mode: "Idle" }, { stale_after: 0.5 }),
    ],
  });
  const m = s.get("live", "Mission");
  assert.equal(m.f.phase, "A", "usb has priority when both are fresh");
  assert.equal(m.f.mode, "Idle");
  assert.equal(m.fs.mode.src, "link");
  assert.equal(m.fs.phase, undefined);
  // the radio field ages on its own: at 10.7 the radio packet (9.9) is stale, usb (10.7 -> 10.2 old) too
  s.setClock("live", 10.4);
  const look = (t, src) => s.get("live", t, src);
  const d = describeValue({ type: "Mission", field: "mode" }, look, 10.4, () => undefined);
  assert.equal(d.state, "fresh");
  const late = describeValue({ type: "Mission", field: "mode" }, look, 10.6, () => undefined);
  assert.equal(late.state, "stale", "judged against the radio packet's own age");
  assert.ok(Math.abs(late.age - 0.7) < 1e-9);
});

test("merge: a stale preferred source gives way to a fresh one; a stale value is kept only when nothing is fresh", () => {
  const s = new Store(() => 0);
  s.applySnapshot("live", {
    clock: 20,
    priority: ["usb", "link"],
    items: [pkt("Attitude", "usb", 10, { omega: [1, 2, 3], q: [1, 0, 0, 0] }, { stale_after: 0.5 }), pkt("Attitude", "link", 19.9, { q: [0, 1, 0, 0] }, { stale_after: 0.5 })],
  });
  const a = s.get("live", "Attitude");
  assert.deepEqual(a.f.q, [0, 1, 0, 0], "usb is stale, the radio q is used");
  assert.deepEqual(a.f.omega, [1, 2, 3], "omega only exists on the stale usb packet: shown, but stale");
  const d = describeValue({ type: "Attitude", field: "omega", kind: "vec" }, (t, src) => s.get("live", t, src), 20, () => undefined);
  assert.equal(d.state, "stale");
});

test("merge: a field missing in all sources says it is not sent; one source behaves as before", () => {
  const s = new Store(() => 0);
  s.applySnapshot("live", { clock: 10, priority: ["usb", "link"], items: [usbAct(9.9), pkt("Actuators", "link", 9.9, { thrust: 0 }, { stale_after: 0.5 })] });
  const look = (t, src) => s.get("live", t, src);
  const d = describeValue({ type: "Actuators", field: "roll_torque" }, look, 10, () => undefined);
  assert.equal(d.text, "not sent");
  assert.match(d.title, /not sent by the vehicle/);
  const one = new Store(() => 0);
  one.applySnapshot("live", { clock: 10, items: [usbAct(9.9)] });
  const entry = one.get("live", "Actuators");
  assert.equal(entry, one.get("live", "Actuators", "usb"), "the same entry object");
  const d1 = describeValue({ type: "Actuators", field: "roll_torque" }, (t, src) => one.get("live", t, src), 10, () => undefined);
  assert.equal(d1.text, "n/s");
});
