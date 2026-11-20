import test from "node:test";
import assert from "node:assert/strict";
import { describeAir, describeRates, describeRow, fmtBytes, fmtHz } from "../app/rates/rates_logic.js";

const row = (o = {}) => ({ source: "usb", type: "POSE", node: 1, path: "usb direct", nominal_hz: 100, age_s: 0.02, count: 5, rate_hz: [99, 100], bytes_s: [6000, 6000], payload_s: [5000, 5000], ...o });

test("number formats", () => {
  assert.equal(fmtHz(99.6), "100");
  assert.equal(fmtHz(2.34), "2.3");
  assert.equal(fmtHz(0.5), "0.50");
  assert.equal(fmtBytes(512.4), "512 B/s");
  assert.equal(fmtBytes(12345), "12.3 kB/s");
  assert.equal(fmtBytes(null), "n/a");
});

test("a row at its nominal rate is fresh without a warning; far below it warns", () => {
  assert.deepEqual([describeRow(row()).state, describeRow(row()).tone], ["fresh", ""]);
  const low = describeRow(row({ rate_hz: [40, 45] }));
  assert.equal(low.tone, "warn");
  assert.match(low.title, /below the nominal/);
  assert.equal(describeRow(row({ nominal_hz: null, rate_hz: [3, 3] })).tone, "");
});

test("not seen for a while, or a zero rate, is stale and not a warning", () => {
  assert.equal(describeRow(row({ age_s: 5 })).state, "stale");
  assert.equal(describeRow(row({ rate_hz: [0, 0], age_s: 1 })).state, "stale");
  assert.equal(describeRow(row({ age_s: 5 })).tone, "");
  // 0.5 Hz stream: three periods (6 s) before it is stale; judged on the 10 s window, 0.5 Hz is not "low"
  const slow = describeRow(row({ nominal_hz: 0.5, rate_hz: [0, 0.5], age_s: 1.9 }));
  assert.deepEqual([slow.state, slow.tone], ["fresh", ""]);
});

test("bytes are n/a when the core did not measure them (packet level)", () => {
  assert.equal(describeRow(row({ bytes_s: null })).bytesText, "n/a");
});

test("air utilisation: bar fraction, tone and the unknown case", () => {
  const air = (pay, cap = 6100) => describeAir({ air: { capacity_bytes_s: cap, payload_s: [pay, pay], utilisation: [pay / cap, pay / cap] } });
  assert.equal(air(1220).tone, "");
  assert.equal(air(1220).frac, 0.2);
  assert.equal(air(5500).tone, "warn");
  assert.equal(air(7000).tone, "bad");
  assert.match(air(1220).text, /air: 1220 B\/s/);
  assert.equal(describeAir({ air: { capacity_bytes_s: 6100, payload_s: null, utilisation: null } }).known, false);
  assert.equal(describeAir(null).text, "air: n/a");
});

test("describeRates keeps the core's order and formats the path totals", () => {
  const data = {
    measure: "frames",
    streams: [row({ path: "air", type: "AIR_POSE" }), row()],
    paths: [{ path: "air", streams: 1, rate_hz: [10, 10], bytes_s: [1100, 1100], payload_s: [1000, 1000] }],
    air: { capacity_bytes_s: 6100, payload_s: [1000, 1000], utilisation: [0.16, 0.16] },
  };
  const d = describeRates(data);
  assert.deepEqual(d.rows.map((r) => r.path), ["air", "usb direct"]);
  assert.deepEqual([d.paths[0].rateText, d.paths[0].bytesText, d.paths[0].payloadText], ["10", "1100 B/s", "1000 B/s"]);
  assert.equal(describeRates(null).rows.length, 0);
});
