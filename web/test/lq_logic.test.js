import test from "node:test";
import assert from "node:assert/strict";
import { DIRECTIONS, antennaText, chipText, chipTitle, counterText, counterTone, directionState, lqTone } from "../app/link/lq_logic.js";
import { GRAPH_PRESETS, SIGNAL_BY_ID, SIGNAL_TYPES, SIGNALS, presetGraph } from "../app/plot/catalogue.js";
import { describe } from "../app/fmt.js";

const [DOWN, UP] = DIRECTIONS;
const entry = (t, f) => ({ t, f, src: "link" });
const schema = { nominal_rate_hz: 2 }; // stale after max(0.5, 3 / 2) = 1.5 s

test("directions are labelled and tied to the right packet", () => {
  assert.equal(DOWN.title, "Downlink (ship -> GS)");
  assert.equal(DOWN.type, "TxModuleStats");
  assert.equal(UP.title, "Uplink (GS -> ship)");
  assert.equal(UP.type, "RxModuleStats");
});

test("missing direction is no data, never the other direction's value", () => {
  const st = directionState(UP, undefined, 10, schema);
  assert.equal(st.state, "nodata");
  assert.equal(st.badge, "no data");
  assert.deepEqual(st.f, {});
  assert.equal(chipText(st), "UL no data");
  assert.match(chipTitle(st), /Uplink \(GS -> ship\).*No data received/);
});

test("fresh, stale and tones per direction", () => {
  const down = directionState(DOWN, entry(10, { rx_lq: 40, rssi: -62 }), 10.2, schema);
  const up = directionState(UP, entry(10, { rx_lq: 96, rssi: -77 }), 10.2, schema);
  assert.equal(down.state, "fresh");
  assert.equal(down.tone, "bad");
  assert.equal(up.tone, "ok");
  assert.equal(chipText(down), "DL 40%");
  assert.equal(chipText(up), "UL 96%");
  const old = directionState(DOWN, entry(10, { rx_lq: 40 }), 13, schema);
  assert.equal(old.state, "stale");
  assert.equal(old.tone, "", "stale data raises no quality tone");
  assert.match(old.badge, /^STALE 3\.0 s$/);
  assert.equal(chipText(old), "DL stale 40%");
  assert.match(chipTitle(old), /STALE/);
});

test("one direction stale does not make the other stale", () => {
  const a = directionState(DOWN, entry(10, { rx_lq: 90 }), 20, schema);
  const b = directionState(UP, entry(19.9, { rx_lq: 90 }), 20, schema);
  assert.deepEqual([a.state, b.state], ["stale", "fresh"]);
});

test("lq tone thresholds and counters", () => {
  assert.deepEqual([49, 50, 79, 80, undefined].map(lqTone), ["bad", "warn", "warn", "ok", ""]);
  assert.equal(counterText({ frames_expired: 3, frames_dropped: 0 }), "expired 3, dropped 0");
  assert.equal(counterText({}), "expired n/s, dropped n/s");
  assert.equal(counterTone(1), "warn");
  assert.equal(counterTone(0), "");
  assert.equal(antennaText({ rx_antenna: 0, tx_antenna: 1 }), "rx 0 / tx 1");
  assert.equal(antennaText({}), "n/s");
});

test("describe shows the custom missing text", () => {
  const d = describe({ type: "RxModuleStats", field: "rx_lq", missingText: "no data" }, () => undefined, 0);
  assert.equal(d.state, "missing");
  assert.equal(d.text, "no data");
  assert.equal(describe({ type: "X", field: "a" }, () => undefined, 0).text, "—");
});

test("plot catalogue: both directions as separate signals, existing ids intact", () => {
  for (const [id, type, unit] of [
    ["link.dl.lq", "TxModuleStats", "%"], ["link.dl.rssi", "TxModuleStats", "dBm"], ["link.dl.snr", "TxModuleStats", "dB"],
    ["link.ul.lq", "RxModuleStats", "%"], ["link.ul.rssi", "RxModuleStats", "dBm"], ["link.ul.snr", "RxModuleStats", "dB"],
  ]) {
    const s = SIGNAL_BY_ID.get(id);
    assert.ok(s, id);
    assert.equal(s.type, type);
    assert.equal(s.unit, unit);
    assert.match(s.label, /^(Downlink \(ship -> GS\)|Uplink \(GS -> ship\))/);
  }
  assert.equal(SIGNAL_BY_ID.get("link.dl.lq").extract({ rx_lq: 40 }, {}), 40);
  assert.equal(SIGNAL_BY_ID.get("link.ul.rssi").extract({ rssi: -77 }, {}), -77);
  assert.equal(SIGNAL_BY_ID.get("link.ul.rssi").extract({}, {}), undefined);
  assert.ok(SIGNAL_TYPES.includes("TxModuleStats") && SIGNAL_TYPES.includes("RxModuleStats"));
  for (const id of ["att.roll", "pos.U", "timing.avg.0", "gnss.home.N"]) assert.ok(SIGNAL_BY_ID.get(id), id);
  assert.equal(new Set(SIGNALS.map((s) => s.id)).size, SIGNALS.length, "ids unique");
});

test("preset graph Link quality up/down", () => {
  const p = presetGraph("link_lq");
  assert.ok(p);
  assert.equal(GRAPH_PRESETS.find((g) => g.id === "link_lq").title, "Link quality up/down (LQ)");
  assert.deepEqual(p.series.map((s) => s.sig), ["link.dl.lq", "link.ul.lq"]);
});
